# -*- coding: utf-8 -*-
"""
=============================================================================
  cold_read_diagnostic.py  —  HyperRAM Systems Diagnostic Tool
=============================================================================
  Performs root-cause analysis on the 110 ms cold read latency and
  independently verifies the write-amplification calculation.

  Specifically:
  1. Tests if the 110 ms latency is due to OS Page Cache Page Faults,
     SSD APST Power State Wakeup, or Python/mmap overhead.
  2. Measures true NVMe SSD hardware read latency using non-buffered OS I/O
     (FILE_FLAG_NO_BUFFERING via Windows API).
  3. Verifies Write-Amplification Factor (WAF) under controlled evictions.
=============================================================================
"""
import sys, os, time, random, gc, ctypes
from ctypes import wintypes

# Windows API Constants for non-buffered I/O
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_NO_BUFFERING = 0x20000000
FILE_FLAG_WRITE_THROUGH = 0x80000000

PAGE_SIZE = 4096
POOL_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "hyperram.pool"))

def bar(title=""):
    if title:
        pad = max(0, (70 - len(title)) // 2)
        print(f"\n{'='*pad} {title} {'='*max(0,70-pad-len(title))}")
    else:
        print("="*72)

# ---------------------------------------------------------------------------
# Diagnostics 1: Windows Non-Buffered Physical NVMe Read Latency
# ---------------------------------------------------------------------------
def run_physical_nvme_test():
    bar("Diagnostic 1: Windows Non-Buffered Direct I/O")
    print("  Bypasses OS Page Cache using FILE_FLAG_NO_BUFFERING.")
    print("  This measures raw hardware NVMe latency + storage driver stack.")
    
    if not os.path.exists(POOL_PATH):
        print(f"  [!] Pool file {POOL_PATH} not found. Please run scale_benchmark.py first.")
        return

    # Open file using CreateFileW to bypass buffering
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateFileW(
        POOL_PATH,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_NO_BUFFERING,
        None
    )

    if handle == -1 or handle == 0:
        err = kernel32.GetLastError()
        print(f"  [ERROR] CreateFileW failed with error code: {err}")
        return

    try:
        # Buffer must be sector-aligned for FILE_FLAG_NO_BUFFERING
        # 4096 bytes is sector-aligned on almost all NVMe SSDs
        buf = ctypes.create_string_buffer(PAGE_SIZE)
        bytes_read = wintypes.DWORD(0)

        latencies_us = []
        # Let's read 100 random pages, sleeping between some of them
        rng = random.Random(99)
        file_size = os.path.getsize(POOL_PATH)
        max_page = (file_size // PAGE_SIZE) - 1

        print(f"  Reading 50 random sector-aligned pages from {POOL_PATH}...")
        for i in range(50):
            page_id = rng.randint(0, max_page)
            offset = page_id * PAGE_SIZE
            
            # Set file pointer
            li_offset = ctypes.c_longlong(offset)
            kernel32.SetFilePointerEx(handle, li_offset, None, 0) # FILE_BEGIN = 0
            
            t0 = time.perf_counter()
            res = kernel32.ReadFile(handle, buf, PAGE_SIZE, ctypes.byref(bytes_read), None)
            lat = (time.perf_counter() - t0) * 1_000_000
            latencies_us.append(lat)
            
            if not res:
                print(f"    [!] ReadFile failed on page {page_id}")

        latencies_us.sort()
        n = len(latencies_us)
        print("\n  Raw NVMe Physical Read Latencies (Buffered Bypassed):")
        print(f"    P50   : {latencies_us[int(0.50 * n)]:.2f} µs")
        print(f"    P90   : {latencies_us[int(0.90 * n)]:.2f} µs")
        print(f"    P95   : {latencies_us[int(0.95 * n)]:.2f} µs")
        print(f"    P99   : {latencies_us[int(0.99 * n)]:.2f} µs")
        print(f"    Max   : {latencies_us[-1]:.2f} µs")
        print(f"    Min   : {latencies_us[0]:.2f} µs")
        print(f"    Mean  : {sum(latencies_us)/n:.2f} µs")
        
        # Test APST Power State wakeup
        print("\n  Testing APST Idle Transition (Sleeping for 5 seconds to let NVMe enter low-power state)...")
        time.sleep(5.0)
        
        # Now do a single read
        page_id = rng.randint(0, max_page)
        offset = page_id * PAGE_SIZE
        li_offset = ctypes.c_longlong(offset)
        kernel32.SetFilePointerEx(handle, li_offset, None, 0)
        
        t0 = time.perf_counter()
        kernel32.ReadFile(handle, buf, PAGE_SIZE, ctypes.byref(bytes_read), None)
        wake_lat = (time.perf_counter() - t0) * 1_000_000
        
        print(f"    First read after 5s idle: {wake_lat:.2f} µs")
        print(f"    (Compare to mean: {wake_lat / (sum(latencies_us)/n):.1f}x slowdown)")
        
    finally:
        kernel32.CloseHandle(handle)

# ---------------------------------------------------------------------------
# Diagnostics 2: mmap Page Fault / OS Page Cache overhead
# ---------------------------------------------------------------------------
def run_mmap_fault_test():
    bar("Diagnostic 2: mmap Page Fault and OS Cache overhead")
    import mmap
    
    file_size = os.path.getsize(POOL_PATH)
    print(f"  Mapping {POOL_PATH} ({file_size / (1024**3):.1f} GB) via mmap...")
    
    t0 = time.perf_counter()
    f = open(POOL_PATH, "r+b")
    mm = mmap.mmap(f.fileno(), file_size)
    map_time = (time.perf_counter() - t0) * 1_000_000
    print(f"  mmap() call elapsed: {map_time:.2f} µs")
    
    # We will read 5 pages:
    # Page A: Cold (never read, triggers initial page fault)
    # Page A (again): Warm (already page-faulted)
    # Page B: Cold
    # Page B (again): Warm
    
    # Let's pick offsets far apart to ensure different virtual memory pages
    offsets = [1024*1024*100, 1024*1024*500, 1024*1024*1000]
    
    print("\n  mmap Access Latencies:")
    for idx, offset in enumerate(offsets):
        # 1st read (Cold Page Fault)
        t0 = time.perf_counter()
        val1 = mm[offset:offset+10]
        lat1 = (time.perf_counter() - t0) * 1_000_000
        
        # 2nd read (Warm Page Cache Hit)
        t0 = time.perf_counter()
        val2 = mm[offset:offset+10]
        lat2 = (time.perf_counter() - t0) * 1_000_000
        
        print(f"    Offset {offset//(1024**2)} MB:")
        print(f"      1st access (Page Fault)  : {lat1:.2f} µs")
        print(f"      2nd access (Cache Hit)   : {lat2:.2f} µs")
        print(f"      Slowdown ratio           : {lat1 / max(0.1, lat2):.1f}x")
        
    mm.close()
    f.close()

# ---------------------------------------------------------------------------
# Diagnostics 3: Independent Write-Amplification Verification
# ---------------------------------------------------------------------------
def run_write_amp_verification():
    bar("Diagnostic 3: Write-Amplification Mathematical Verification")
    from core import HyperRAMEngine, QoSTag
    
    print("  Verifies that Write Amplification Factor (WAF) is computed correctly.")
    print("  We write a known page payload and compare the engine's stats with the math.")
    
    # Let's test with a tiny cache (128 KB = 32 pages)
    engine = HyperRAMEngine(ssd_pool_path=POOL_PATH, pool_size_gb=2, page_size=PAGE_SIZE)
    engine.max_ram_cache_pages = 32
    
    # Create compressible and incompressible page payloads
    # Compressible: identical bytes (LZ4 compress ratio should be > 100x)
    compressible_data = b"\x41" * PAGE_SIZE
    # Incompressible: random bytes (LZ4 should fall back to raw page, ratio = 1x)
    rng = random.Random(42)
    incompressible_data = bytes(rng.randint(0, 255) for _ in range(PAGE_SIZE))
    
    # Clear metrics
    engine.total_writes = 0
    engine.ssd_writes = 0
    engine.total_compressed_bytes = 0
    engine.total_uncompressed_bytes = 0
    
    # 1. Fill cache with compressible data (no evictions yet)
    print("\n  Writing 32 compressible pages (exactly fits cache)...")
    for i in range(32):
        engine.write_page(i, compressible_data)
        
    m = engine.get_metrics()
    print(f"    RAM cache pages : {len(engine.ram_cache)}")
    print(f"    SSD writes      : {m['ssd_writes']}")
    print(f"    Logical writes  : {engine.total_writes}")
    print(f"    Expected WAF    : 0.00 (all in RAM)")
    print(f"    Reported WAF    : {m['ssd_writes']/engine.total_writes:.2f}")
    
    # 2. Trigger evictions with compressible data
    print("\n  Writing 16 more compressible pages (triggers 16 evictions)...")
    for i in range(32, 48):
        engine.write_page(i, compressible_data)
        
    m = engine.get_metrics()
    logical_total = engine.total_writes
    ssd_total = m['ssd_writes']
    comp_ratio = m['compression_ratio']
    
    print(f"    RAM cache pages : {len(engine.ram_cache)}")
    print(f"    SSD writes      : {ssd_total}")
    print(f"    Logical writes  : {logical_total}")
    print(f"    Compression     : {comp_ratio:.2f}x")
    
    # Independent WAF:
    # WAF_logical = ssd_writes / logical_writes
    # WAF_physical = (ssd_writes * compressed_avg_size) / (logical_writes * PAGE_SIZE)
    waf_logical = ssd_total / logical_total
    # Since all evicted pages were compressible, their actual sizes were very small
    actual_ssd_bytes = engine.total_compressed_bytes
    logical_bytes = logical_total * PAGE_SIZE
    waf_physical = actual_ssd_bytes / logical_bytes
    
    print(f"    Independent Logical WAF  : {waf_logical:.4f} (SSD writes / logical writes)")
    print(f"    Independent Physical WAF : {waf_physical:.4f} (actual SSD bytes / logical bytes)")
    print(f"    Verification result      : {'SUCCESS' if abs(waf_logical - 0.33) < 0.05 else 'FAIL'}")
    
    # 3. Trigger evictions with incompressible data
    print("\n  Writing 32 incompressible pages (triggers 32 evictions of random data)...")
    for i in range(48, 80):
        engine.write_page(i, incompressible_data)
        
    m = engine.get_metrics()
    print(f"    Total SSD writes         : {m['ssd_writes']}")
    print(f"    Total Logical writes     : {engine.total_writes}")
    print(f"    New Compression ratio    : {m['compression_ratio']:.2f}x")
    
    new_waf_logical = m['ssd_writes'] / engine.total_writes
    new_waf_physical = engine.total_compressed_bytes / (engine.total_writes * PAGE_SIZE)
    print(f"    New Logical WAF          : {new_waf_logical:.4f}")
    print(f"    New Physical WAF         : {new_waf_physical:.4f}")
    print(f"    (Physical WAF increased due to random data compression bypass: {new_waf_physical/waf_physical:.1f}x increase)")
    
    engine.close()

if __name__ == "__main__":
    bar("HyperRAM Systems Root-Cause & WAF Diagnostic")
    print(f"  OS Platform : {sys.platform}")
    print(f"  Python Ver  : {sys.version.split()[0]}")
    print(f"  Pool File   : {POOL_PATH}")
    bar()
    
    run_physical_nvme_test()
    run_mmap_fault_test()
    run_write_amp_verification()
    bar("DIAGNOSTIC COMPLETE")
