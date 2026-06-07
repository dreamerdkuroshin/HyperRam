# -*- coding: utf-8 -*-
r"""
=============================================================================
  kernel_benchmark.py  —  HyperRAM Kernel-Path vs Userspace-Path Comparison
=============================================================================
  Measures and compares:
    - Kernel path latency  (\\.\HyperRAM via kernel_client.py)
    - Userspace path latency  (core.py + mmap NVMe pool)

  Produces two CSV outputs and one summary table for the paper.

  Usage:
    venv\\Scripts\\python.exe kernel_benchmark.py
    venv\\Scripts\\python.exe kernel_benchmark.py --kernel-only
    venv\\Scripts\\python.exe kernel_benchmark.py --userspace-only
    venv\\Scripts\\python.exe kernel_benchmark.py --pages 2000

  NOTE: The kernel driver must be loaded in Test Mode for kernel measurements.
        Run simulate_test_mode.ps1 to check prerequisites first.
        If the driver is absent the kernel path silently falls back to
        the userspace engine -- mark those runs as "fallback" in the paper.
=============================================================================
"""
import sys, os
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import time, random, statistics, csv, argparse, gc
sys.path.insert(0, os.path.dirname(__file__))

PAGE_SIZE  = 4096
SEP        = "=" * 72
DASH       = "-" * 72

def fmt(v):   return f"{v:>10.3f}"
def pct(v):   return f"{v:>7.2f}%"

# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------
def percentiles(data, pcts=(50, 90, 95, 99, 99.9)):
    if not data:
        return {p: 0.0 for p in pcts}
    s = sorted(data)
    n = len(s)
    return {p: s[min(int(p / 100 * n), n - 1)] for p in pcts}


def classify(lat_us, thresh=500.0):
    """Heuristic: < 500 µs → RAM hit, else → NVMe miss."""
    return lat_us < thresh


# ---------------------------------------------------------------------------
# Single benchmark run: measure N reads/writes on a given engine/client
# ---------------------------------------------------------------------------
def run_latency_benchmark(read_fn, write_fn, n_pages, n_reads, label, rng_seed=42):
    """
    Fill n_pages, then perform n_reads with a Zipf 80/20 pattern.

    Returns dict with hit_rate, avg_us, p50/p99/p999, ram_lats, nvme_lats.
    """
    print(f"\n  [{label}] Filling {n_pages} pages...", flush=True)
    t_fill = time.perf_counter()
    for i in range(n_pages):
        write_fn(i, bytes([i & 0xFF]) * PAGE_SIZE)
    fill_s = time.perf_counter() - t_fill
    fill_mb_s = (n_pages * PAGE_SIZE / (1024**2)) / fill_s if fill_s > 0 else 0
    print(f"  [{label}] Fill done: {fill_s:.2f}s  ({fill_mb_s:.1f} MB/s)")

    print(f"  [{label}] Reading {n_reads} pages (80/20 Zipf)...", flush=True)
    hot = max(1, n_pages // 5)
    rng = random.Random(rng_seed)
    ram_lats, nvme_lats = [], []

    for _ in range(n_reads):
        pid = rng.randint(0, hot - 1) if rng.random() < 0.80 else rng.randint(0, n_pages - 1)
        t0  = time.perf_counter()
        read_fn(pid)
        lat = (time.perf_counter() - t0) * 1_000_000
        (ram_lats if classify(lat) else nvme_lats).append(lat)

    total = len(ram_lats) + len(nvme_lats)
    hr    = len(ram_lats) / max(1, total) * 100
    all_l = ram_lats + nvme_lats
    pcts  = percentiles(all_l, (50, 90, 95, 99, 99.9))
    avg   = statistics.mean(all_l) if all_l else 0
    ram_avg  = statistics.mean(ram_lats)  if ram_lats  else 0
    nvme_avg = statistics.mean(nvme_lats) if nvme_lats else 0
    eff_lat  = (hr / 100) * ram_avg + (1 - hr / 100) * nvme_avg

    return {
        "label":    label,
        "n_pages":  n_pages,
        "n_reads":  n_reads,
        "hit_rate": hr,
        "avg_us":   avg,
        "eff_us":   eff_lat,
        "ram_avg":  ram_avg,
        "nvme_avg": nvme_avg,
        "fill_mb_s": fill_mb_s,
        "p50":   pcts[50],
        "p90":   pcts[90],
        "p95":   pcts[95],
        "p99":   pcts[99],
        "p999":  pcts[99.9],
        "ram_lats":  ram_lats,
        "nvme_lats": nvme_lats,
    }


# ---------------------------------------------------------------------------
# IOCTL overhead micro-benchmark (DeviceIoControl round-trip without data)
# ---------------------------------------------------------------------------
def measure_ioctl_overhead(client, n_iter=2000):
    """Measure the round-trip cost of IOCTL_GET_STATS (tiny in/out buffers)."""
    if not hasattr(client, 'get_stats'):
        return None
    lats = []
    for _ in range(n_iter):
        t0 = time.perf_counter()
        client.get_stats()
        lats.append((time.perf_counter() - t0) * 1_000_000)
    return lats


# ---------------------------------------------------------------------------
# Print comparison table
# ---------------------------------------------------------------------------
def print_comparison(results):
    print(f"\n{SEP}")
    print("  Kernel vs Userspace — Comparison Table")
    print(SEP)
    hdr = (f"  {'Path':<22} | {'HR%':>6} | {'RAM avg µs':>10} | {'NVMe avg µs':>11} | "
           f"{'P50 µs':>8} | {'P99 µs':>8} | {'P99.9 µs':>9}")
    print(hdr)
    print(f"  {DASH}")
    for r in results:
        nvme_s = f"{r['nvme_avg']:>11.3f}" if r['nvme_lats'] else f"{'N/A':>11}"
        print(
            f"  {r['label']:<22} | {r['hit_rate']:>5.1f}% | {r['ram_avg']:>10.3f} | "
            f"{nvme_s} | {r['p50']:>8.3f} | {r['p99']:>8.3f} | {r['p999']:>9.3f}"
        )
    print()

    if len(results) >= 2:
        k = next((r for r in results if 'Kernel' in r['label']), None)
        u = next((r for r in results if 'Userspace' in r['label']), None)
        if k and u and k['ram_avg'] > 0 and u['ram_avg'] > 0:
            delta_ram  = k['ram_avg']  - u['ram_avg']
            delta_p99  = k['p99']      - u['p99']
            print(f"  Kernel RAM overhead vs userspace: {delta_ram:+.3f} µs avg, {delta_p99:+.3f} µs P99")
            print(f"  (positive = kernel is slower; negative = kernel is faster)")
            print()


# ---------------------------------------------------------------------------
# Save results to CSV
# ---------------------------------------------------------------------------
def save_csv(results, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    ts   = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"kernel_vs_userspace_{ts}.csv")

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path", "n_pages", "n_reads", "hit_rate_pct",
                    "ram_avg_us", "nvme_avg_us", "eff_us", "fill_mb_s",
                    "p50_us", "p90_us", "p95_us", "p99_us", "p999_us"])
        for r in results:
            w.writerow([
                r["label"], r["n_pages"], r["n_reads"],
                f"{r['hit_rate']:.4f}",
                f"{r['ram_avg']:.4f}",
                f"{r['nvme_avg']:.4f}" if r['nvme_lats'] else "N/A",
                f"{r['eff_us']:.4f}",
                f"{r['fill_mb_s']:.2f}",
                f"{r['p50']:.4f}", f"{r['p90']:.4f}",
                f"{r['p95']:.4f}", f"{r['p99']:.4f}", f"{r['p999']:.4f}",
            ])
    print(f"  [csv] Saved → {path}")
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="HyperRAM Kernel vs Userspace Benchmark")
    parser.add_argument("--pages",          type=int, default=512,
                        help="Pages to fill per engine (default: 512 = 2 MB)")
    parser.add_argument("--reads",          type=int, default=2000,
                        help="Read operations per engine (default: 2000)")
    parser.add_argument("--kernel-only",    action="store_true",
                        help="Only run kernel path")
    parser.add_argument("--userspace-only", action="store_true",
                        help="Only run userspace path")
    parser.add_argument("--ioctl-overhead", action="store_true",
                        help="Also run IOCTL GET_STATS overhead micro-benchmark")
    parser.add_argument("--output-dir",     default=None,
                        help="Directory to save CSV (default: ../results/)")
    args = parser.parse_args()

    results_dir = args.output_dir or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "results"))

    print(f"\n{SEP}")
    print("  HyperRAM — Kernel vs Userspace Latency Benchmark")
    print(SEP)
    print(f"  Pages      : {args.pages}  ({args.pages * PAGE_SIZE // 1024} KB working set)")
    print(f"  Reads      : {args.reads}")
    print(f"  Timestamp  : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    all_results = []

    # ── Kernel path ────────────────────────────────────────────────────────
    if not args.userspace_only:
        print(f"\n{DASH}")
        print("  PATH 1: Kernel Driver  (\\\\.\\\\.\\HyperRAM)")
        print(DASH)
        try:
            from kernel_client import HyperRAMKernelClient
            kc = HyperRAMKernelClient()
            mode = "Kernel (HyperRAM.sys)" if kc.is_kernel_mode else "Kernel-Fallback (userspace)"
            print(f"  Mode: {mode}")
            if not kc.is_kernel_mode:
                print("  NOTE: Driver not loaded. Measurements reflect userspace fallback, not real kernel path.")
                print("        To load driver: run install_and_start.ps1 as Administrator in Test Mode.")

            res = run_latency_benchmark(
                read_fn  = lambda pid: kc.read_page(pid),
                write_fn = lambda pid, d: kc.write_page(pid, d),
                n_pages  = args.pages,
                n_reads  = args.reads,
                label    = mode,
            )
            all_results.append(res)

            if args.ioctl_overhead and kc.is_kernel_mode:
                print(f"\n  IOCTL GET_STATS overhead (2000 calls)...")
                ioctl_lats = measure_ioctl_overhead(kc)
                if ioctl_lats:
                    ip = percentiles(ioctl_lats, (50, 99))
                    print(f"  IOCTL round-trip: avg={statistics.mean(ioctl_lats):.3f} µs  "
                          f"P50={ip[50]:.3f} µs  P99={ip[99]:.3f} µs")

            kc.close()
            gc.collect()

        except ImportError as e:
            print(f"  [skip] kernel_client.py not importable: {e}")
        except Exception as e:
            import traceback
            print(f"  [error] Kernel benchmark failed: {e}")
            traceback.print_exc()

    # ── Userspace path ─────────────────────────────────────────────────────
    if not args.kernel_only:
        print(f"\n{DASH}")
        print("  PATH 2: Userspace Engine  (core.py + mmap NVMe pool)")
        print(DASH)
        try:
            from core import HyperRAMEngine, QoSTag
            pool_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "hyperram.pool"))
            pool_gb = max(2, int(os.path.getsize(pool_path) / (1024**3))) \
                      if os.path.exists(pool_path) else 2

            eng = HyperRAMEngine(ssd_pool_path=pool_path, pool_size_gb=pool_gb,
                                 page_size=PAGE_SIZE)
            # Match kernel driver's 4 MB RAM cache
            eng.max_ram_cache_pages = 4 * 1024 * 1024 // PAGE_SIZE  # 1024 pages

            res = run_latency_benchmark(
                read_fn  = lambda pid: eng.read_page(pid),
                write_fn = lambda pid, d: eng.write_page(pid, d),
                n_pages  = args.pages,
                n_reads  = args.reads,
                label    = "Userspace (core.py + NVMe)",
            )
            all_results.append(res)
            eng.close()
            gc.collect()

        except Exception as e:
            import traceback
            print(f"  [error] Userspace benchmark failed: {e}")
            traceback.print_exc()

    # ── Print & save ───────────────────────────────────────────────────────
    if all_results:
        print_comparison(all_results)

        print(f"\n{DASH}")
        print("  Paper notes:")
        print("  • Kernel RAM hit latency = IRP dispatch + spin lock + RtlCopyMemory(4 KB)")
        print("  • Userspace RAM hit latency = Python call + mmap dict lookup + memcpy")
        print("  • Kernel 'NVMe miss' = KeStall(50 µs) — NOT real PCIe latency")
        print("  • Userspace 'NVMe miss' = mmap page fault + OS disk I/O — real NVMe")
        print("  • For paper Fig comparison: kernel hit ≈ userspace hit ± context-switch cost")
        print(f"  • Working-set: {args.pages} pages = {args.pages * PAGE_SIZE // 1024} KB")
        print(f"    (kernel driver capacity: 8192 slots × 4 KB = 32 MB)")
        print(DASH)

        save_csv(all_results, results_dir)
    else:
        print("  No results collected.")


if __name__ == "__main__":
    main()
