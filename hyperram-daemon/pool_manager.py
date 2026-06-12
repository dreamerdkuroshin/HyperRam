# -*- coding: utf-8 -*-
"""
=============================================================================
  pool_manager.py  —  HyperRAM NVMe Pool File Management
=============================================================================
  Utilities for growing, shrinking, checkpointing, and defragmenting the
  hyperram.pool file that backs the NVMe tier.

  Usage:
    python pool_manager.py --info
    python pool_manager.py --grow 10
    python pool_manager.py --checkpoint save
    python pool_manager.py --checkpoint load
    python pool_manager.py --self-test
=============================================================================
"""
import sys
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


import os, json, time, shutil, struct, hashlib, argparse, gc
from pathlib import Path

PAGE_SIZE = 4096
DEFAULT_POOL = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "hyperram.pool")
)
DEFAULT_META = DEFAULT_POOL + ".meta.json"

SEP  = "=" * 64
DASH = "-" * 64

# ---------------------------------------------------------------------------
# Disk-space helpers
# ---------------------------------------------------------------------------
def disk_free_gb(path):
    """Return free disk space in GB for the drive containing `path`."""
    usage = shutil.disk_usage(os.path.dirname(os.path.abspath(path)))
    return usage.free / (1024 ** 3)

def pool_size_gb(path):
    """Current pool file size in GB."""
    if not os.path.exists(path):
        return 0.0
    return os.path.getsize(path) / (1024 ** 3)

def pool_info(path=DEFAULT_POOL):
    """Print a human-readable summary of the pool file."""
    print(f"\n{SEP}")
    print(f"  Pool File Info")
    print(SEP)
    if not os.path.exists(path):
        print(f"  [!] Pool file not found: {path}")
        return
    sz_gb  = pool_size_gb(path)
    free   = disk_free_gb(path)
    mtime  = os.path.getmtime(path)
    meta   = _load_meta(path)
    print(f"  Path      : {path}")
    print(f"  Size      : {sz_gb:.3f} GB  ({os.path.getsize(path):,} bytes)")
    print(f"  Disk free : {free:.2f} GB")
    print(f"  Modified  : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime))}")
    if meta:
        print(f"  Pages     : {meta.get('total_pages', 'N/A')} tracked in checkpoint")
        print(f"  Checksum  : {meta.get('pool_checksum', 'N/A')[:16]}...")
        print(f"  Saved at  : {meta.get('saved_at', 'N/A')}")
    else:
        print(f"  Checkpoint: none")
    print(SEP + "\n")

# ---------------------------------------------------------------------------
# Pool grow / shrink
# ---------------------------------------------------------------------------
def grow_pool(path, target_gb, verbose=True):
    """
    Grow the pool file to `target_gb` GB without disturbing existing data.
    Returns the new file size in bytes.
    Raises RuntimeError if there is insufficient disk space.
    """
    target_bytes = int(target_gb * 1024 ** 3)
    current_bytes = os.path.getsize(path) if os.path.exists(path) else 0

    if target_bytes <= current_bytes:
        if verbose:
            print(f"  [pool_manager] Pool already {current_bytes/(1024**3):.2f} GB — no grow needed.")
        return current_bytes

    delta_gb = (target_bytes - current_bytes) / (1024 ** 3)
    free     = disk_free_gb(path)

    if verbose:
        print(f"  [pool_manager] Growing pool: "
              f"{current_bytes/(1024**3):.2f} GB → {target_gb:.1f} GB "
              f"(+{delta_gb:.2f} GB needed, {free:.2f} GB free)")

    if free < delta_gb + 0.5:   # 0.5 GB safety margin
        raise RuntimeError(
            f"Insufficient disk space: need {delta_gb+0.5:.2f} GB, "
            f"only {free:.2f} GB free on {os.path.splitdrive(path)[0]}")

    # Extend the file by seeking to new end and writing a null byte
    t0 = time.perf_counter()
    with open(path, 'r+b') as f:
        f.seek(target_bytes - 1)
        f.write(b'\x00')
    elapsed = time.perf_counter() - t0

    actual = os.path.getsize(path)
    if verbose:
        print(f"  [pool_manager] Grow complete in {elapsed:.2f}s. "
              f"New size: {actual/(1024**3):.3f} GB")
    return actual


def shrink_pool(path, target_gb, verbose=True):
    """
    Shrink (truncate) the pool file to `target_gb` GB.
    WARNING: truncates data beyond target_gb. Ensure pages in that region
    are evicted before calling this.
    """
    target_bytes = int(target_gb * 1024 ** 3)
    current_bytes = os.path.getsize(path) if os.path.exists(path) else 0

    if target_bytes >= current_bytes:
        if verbose:
            print(f"  [pool_manager] Pool is {current_bytes/(1024**3):.2f} GB — "
                  f"cannot shrink to {target_gb:.1f} GB (already smaller).")
        return current_bytes

    if verbose:
        print(f"  [pool_manager] Shrinking pool: "
              f"{current_bytes/(1024**3):.2f} GB → {target_gb:.1f} GB")

    with open(path, 'r+b') as f:
        f.truncate(target_bytes)

    actual = os.path.getsize(path)
    if verbose:
        print(f"  [pool_manager] Shrink done. New size: {actual/(1024**3):.3f} GB")
    return actual

# ---------------------------------------------------------------------------
# Checkpoint — persist page table metadata for crash recovery
# ---------------------------------------------------------------------------
def _load_meta(path):
    meta_path = path + ".meta.json"
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_checkpoint(engine, pool_path=DEFAULT_POOL, verbose=True):
    """
    Serialise the engine's page_table + metrics to a JSON sidecar file.
    This allows crash recovery: after restart, call load_checkpoint()
    and the engine can find all pages in the pool without re-scanning.

    File: <pool_path>.meta.json
    """
    meta_path = pool_path + ".meta.json"
    t0 = time.perf_counter()

    # Convert page_table to a JSON-serialisable dict
    # key: str(page_id)  → [is_in_ram, qos_tag, compressed_size, ssd_offset]
    serialised = {}
    for pid, entry in engine.page_table.items():
        is_in_ram, qos, comp_sz, ssd_off = entry
        # Only persist pages that are on SSD (in-RAM pages are transient)
        if not is_in_ram:
            serialised[str(pid)] = [False, qos, comp_sz, ssd_off]

    # Compute a lightweight checksum of the pool file header (first 64 KB)
    pool_checksum = "N/A"
    if os.path.exists(pool_path):
        try:
            with open(pool_path, "rb") as pf:
                sample = pf.read(65536)
            pool_checksum = hashlib.sha256(sample).hexdigest()
        except Exception:
            pass

    meta = {
        "saved_at":        time.strftime("%Y-%m-%d %H:%M:%S"),
        "pool_path":       pool_path,
        "pool_size_gb":    pool_size_gb(pool_path),
        "page_size":       engine.page_size,
        "total_pages":     len(serialised),
        "pool_checksum":   pool_checksum,
        "metrics":         engine.get_metrics(),
        "page_table":      serialised,
    }

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    elapsed = time.perf_counter() - t0
    if verbose:
        print(f"  [pool_manager] Checkpoint saved: {len(serialised)} SSD pages "
              f"→ {meta_path}  ({elapsed*1000:.1f} ms)")
    return meta_path


def load_checkpoint(engine, pool_path=DEFAULT_POOL, verbose=True):
    """
    Restore engine.page_table from the JSON sidecar.
    Call this immediately after creating a fresh HyperRAMEngine pointing at
    the same pool file — it re-populates the page_table so pages can be found.

    Returns:
        int: number of pages restored, or 0 if no checkpoint found.
    """
    meta = _load_meta(pool_path)
    if not meta:
        if verbose:
            print(f"  [pool_manager] No checkpoint found for {pool_path}")
        return 0

    # Verify checksum (warn if mismatch — pool may have been modified)
    if os.path.exists(pool_path):
        try:
            with open(pool_path, "rb") as pf:
                sample = pf.read(65536)
            current_cksum = hashlib.sha256(sample).hexdigest()
            if current_cksum != meta.get("pool_checksum", ""):
                print("  [pool_manager] ⚠  Pool checksum mismatch — "
                      "pool may have been modified since checkpoint.")
        except Exception:
            pass

    restored = 0
    t0 = time.perf_counter()
    for pid_str, entry in meta["page_table"].items():
        pid = int(pid_str)
        is_in_ram, qos, comp_sz, ssd_off = entry
        engine.page_table[pid] = (False, qos, comp_sz, ssd_off)
        engine.offset_to_page[ssd_off] = pid
        restored += 1

    elapsed = time.perf_counter() - t0
    if verbose:
        print(f"  [pool_manager] Checkpoint loaded: {restored} pages restored "
              f"({elapsed*1000:.1f} ms)  saved at {meta.get('saved_at','?')}")
    return restored


# ---------------------------------------------------------------------------
# Defrag — compact SSD pages to minimize fragmentation and wasted space
# ---------------------------------------------------------------------------
def defrag_pool(engine, verbose=True):
    """
    Rewrites all SSD pages sequentially from offset 0, eliminating gaps.
    This reduces write amplification on the next fill cycle and improves
    sequential read performance.

    NOTE: This temporarily doubles pool file I/O — do not run during benchmarks.
    """
    if verbose:
        print("  [pool_manager] Starting defragmentation...")

    ssd_pages = {
        pid: entry
        for pid, entry in engine.page_table.items()
        if not entry[0]   # not in RAM
    }

    if not ssd_pages:
        if verbose:
            print("  [pool_manager] No SSD pages to defrag.")
        return

    new_offset = 0
    moved = 0
    t0 = time.perf_counter()

    for pid, (is_in_ram, qos, comp_sz, old_off) in ssd_pages.items():
        if old_off == new_offset:
            new_offset += engine.page_size
            continue
        # Read from old location
        engine.ssd_mmap.seek(old_off)
        data = engine.ssd_mmap.read(comp_sz)
        # Write to new location
        engine.ssd_mmap.seek(new_offset)
        engine.ssd_mmap.write(data)
        # Update page table
        engine.page_table[pid] = (False, qos, comp_sz, new_offset)
        engine.offset_to_page.pop(old_off, None)
        engine.offset_to_page[new_offset] = pid
        new_offset += engine.page_size
        moved += 1

    elapsed = time.perf_counter() - t0
    if verbose:
        reclaimed = (len(ssd_pages) * engine.page_size - new_offset) / (1024 * 1024)
        print(f"  [pool_manager] Defrag done: {moved} pages relocated, "
              f"{reclaimed:.1f} MB reclaimed  ({elapsed*1000:.1f} ms)")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
def self_test():
    print(f"\n{SEP}")
    print("  pool_manager — Self Test")
    print(SEP)

    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from core import HyperRAMEngine

    TEST_POOL = os.path.join(os.path.dirname(__file__), "..", "hyperram.pool")
    TEST_POOL = os.path.abspath(TEST_POOL)
    N_PAGES   = 300

    print(f"\n  [1/5] Pool info before test:")
    pool_info(TEST_POOL)

    print(f"  [2/5] Write {N_PAGES} pages + checkpoint...")
    engine = HyperRAMEngine(ssd_pool_path=TEST_POOL)
    engine.max_ram_cache_pages = 50   # tiny cache → forces SSD spills

    for i in range(N_PAGES):
        engine.write_page(i, bytes([i & 0xFF]) * PAGE_SIZE)
    m = engine.get_metrics()
    print(f"    SSD writes: {m['ssd_writes']}   RAM used: {m['ram_used_mb']:.2f} MB")

    ckpt_path = save_checkpoint(engine, TEST_POOL)
    engine.close()
    gc.collect()

    print(f"\n  [3/5] Simulate crash → fresh engine (no page_table)...")
    engine2 = HyperRAMEngine(ssd_pool_path=TEST_POOL)
    engine2.max_ram_cache_pages = 50
    # Without checkpoint: page 0 should return zeros
    data_no_ckpt = engine2.read_page(0)
    print(f"    Page 0 without checkpoint: "
          f"{'zeros (data lost)' if data_no_ckpt == b'\\x00'*PAGE_SIZE else 'data found'}")
    engine2.close()
    gc.collect()

    print(f"\n  [4/5] Restart with checkpoint loaded...")
    engine3 = HyperRAMEngine(ssd_pool_path=TEST_POOL)
    engine3.max_ram_cache_pages = 50
    n = load_checkpoint(engine3, TEST_POOL)
    print(f"    Restored {n} pages from checkpoint.")
    data_ckpt = engine3.read_page(0)
    expected  = bytes([0]) * PAGE_SIZE
    ok = data_ckpt == expected
    print(f"    Page 0 after recovery: {'✓ CORRECT' if ok else '✗ MISMATCH'}")

    print(f"\n  [5/5] Defrag test...")
    defrag_pool(engine3, verbose=True)
    engine3.close()
    gc.collect()

    print(f"\n  Self-test {'PASSED' if ok else 'FAILED'}")
    print(SEP + "\n")
    return ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HyperRAM Pool Manager")
    parser.add_argument("--pool",       default=DEFAULT_POOL,
                        help="Path to hyperram.pool file")
    parser.add_argument("--info",       action="store_true",
                        help="Print pool file info")
    parser.add_argument("--grow",       type=float, metavar="GB",
                        help="Grow pool to GB size")
    parser.add_argument("--shrink",     type=float, metavar="GB",
                        help="Shrink pool to GB size")
    parser.add_argument("--checkpoint", choices=["save", "load"],
                        help="Save or load page-table checkpoint")
    parser.add_argument("--self-test",  action="store_true",
                        help="Run built-in self-test")
    args = parser.parse_args()

    if args.info or not any([args.grow, args.shrink, args.checkpoint, args.self_test]):
        pool_info(args.pool)

    if args.grow:
        grow_pool(args.pool, args.grow)

    if args.shrink:
        shrink_pool(args.pool, args.shrink)

    if args.checkpoint == "save":
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from core import HyperRAMEngine
        eng = HyperRAMEngine(ssd_pool_path=args.pool)
        save_checkpoint(eng, args.pool)
        eng.close()

    if args.checkpoint == "load":
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from core import HyperRAMEngine
        eng = HyperRAMEngine(ssd_pool_path=args.pool)
        load_checkpoint(eng, args.pool)
        eng.close()

    if args.self_test:
        self_test()
