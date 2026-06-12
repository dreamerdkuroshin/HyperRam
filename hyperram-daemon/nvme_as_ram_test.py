# -*- coding: utf-8 -*-
"""
=============================================================
  HyperRAM -- NVMe-as-RAM Live Benchmark
=============================================================
  This test exercises the full HyperRAMEngine pipeline:
    1. Fill RAM cache until it overflows -> pages spill to NVMe
    2. Read pages back -> some hit RAM cache, rest come from NVMe
    3. Measure per-read latency and classify as RAM hit or NVMe read
    4. Show LRU promotion: cold NVMe page -> hot RAM page on next read
    5. Report final metrics proving NVMe is acting as extended RAM

  All I/O goes through the real mmap-backed hyperram.pool file
  on the NVMe SSD -- no simulated sleep delays.
=============================================================
"""
import io
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import sys, os, time, random, statistics
sys.path.insert(0, os.path.dirname(__file__))
from core import HyperRAMEngine, QoSTag

# ── Configuration ─────────────────────────────────────────────────────────────
RAM_CACHE_GB     = 0.001   # 1 MB RAM cache  (small → easy to overflow into NVMe)
POOL_SIZE_GB     = 2
PAGE_SIZE        = 4096
RAM_CACHE_PAGES  = int(RAM_CACHE_GB * 1024 * 1024 * 1024 / PAGE_SIZE)  # ~256 pages
OVERFLOW_PAGES   = RAM_CACHE_PAGES * 4          # 4× RAM capacity → force NVMe spills
WORKLOAD_READS   = RAM_CACHE_PAGES * 8          # total reads to measure
POOL_PATH        = "hyperram.pool"

# Latency threshold: < 500 µs → RAM hit, else NVMe read
RAM_HIT_THRESH_US = 500.0

BAR  = "=" * 62
DASH = "-" * 62

def fmt_us(v): return f"{v:>9.2f} µs"
def fmt_ns(v): return f"{v:>9.0f} ns"
def pct(a,b):  return f"{100.0*a/b:5.1f}%" if b else "  N/A"

print(f"\n{BAR}")
print(f"  HyperRAM -- NVMe Running as RAM  (End-to-End Test)")
print(f"{BAR}")
print(f"  RAM cache limit  : {RAM_CACHE_PAGES} pages  ({RAM_CACHE_GB*1024:.0f} MB)")
print(f"  NVMe pool file   : {os.path.abspath(POOL_PATH)}")
print(f"  Pool size        : {POOL_SIZE_GB} GB")
print(f"  Overflow writes  : {OVERFLOW_PAGES} pages  ({OVERFLOW_PAGES*PAGE_SIZE//1024} KB)")
print(f"  Read workload    : {WORKLOAD_READS} reads")
print(f"{DASH}\n")

# ── Phase 0: Init engine with a tiny RAM cache ────────────────────────────────
print("[0/5] Initialising HyperRAM engine...")
engine = HyperRAMEngine(
    ssd_pool_path=POOL_PATH,
    pool_size_gb=POOL_SIZE_GB,
    page_size=PAGE_SIZE
)
# Override to a small cache so we can overflow it easily
engine.max_ram_cache_pages = RAM_CACHE_PAGES
print(f"      Engine ready. RAM cache: {RAM_CACHE_PAGES} pages, "
      f"NVMe pool: {POOL_SIZE_GB} GB\n")

# ── Phase 1: Write OVERFLOW_PAGES pages (forces evictions → NVMe) ─────────────
print(f"[1/5] Writing {OVERFLOW_PAGES} pages to fill RAM and overflow to NVMe...")
t_write_start = time.perf_counter()
for i in range(OVERFLOW_PAGES):
    data = (bytes([i & 0xFF]) * PAGE_SIZE)
    qos  = QoSTag.AI if i % 3 == 0 else (QoSTag.TEXTURE if i % 3 == 1 else QoSTag.DEFAULT)
    engine.write_page(i, data, qos)

t_write_end = time.perf_counter()
write_s = t_write_end - t_write_start
metrics_after_write = engine.get_metrics()

print(f"      Done in {write_s*1000:.1f} ms")
print(f"      RAM cache used   : {metrics_after_write['ram_used_mb']:.2f} MB "
      f"({len(engine.ram_cache)} pages)")
print(f"      NVMe pool used   : {metrics_after_write['ssd_used_mb']:.2f} MB "
      f"({len(engine.page_table)-len(engine.ram_cache)} pages on NVMe)")
print(f"      SSD writes so far: {metrics_after_write['ssd_writes']}")
print()

if metrics_after_write['ssd_writes'] == 0:
    print("  [WARN] No SSD writes occurred — RAM cache not overflowed yet.")
    print("         Increasing overflow factor …")
    # Double the writes to ensure overflow
    for i in range(OVERFLOW_PAGES, OVERFLOW_PAGES * 2):
        engine.write_page(i, bytes([i & 0xFF]) * PAGE_SIZE, QoSTag.DEFAULT)
    OVERFLOW_PAGES *= 2
    metrics_after_write = engine.get_metrics()
    print(f"      NVMe pool used   : {metrics_after_write['ssd_used_mb']:.2f} MB")
    print()

# ── Phase 2: Sequential read pass — measure per-page latency ──────────────────
print(f"[2/5] Sequential read pass ({OVERFLOW_PAGES} pages) -- measuring RAM vs NVMe latency...")
seq_latencies_ram  = []
seq_latencies_nvme = []
seq_hit = seq_miss = 0

for i in range(OVERFLOW_PAGES):
    t0 = time.perf_counter()
    engine.read_page(i)
    lat_us = (time.perf_counter() - t0) * 1_000_000

    if lat_us < RAM_HIT_THRESH_US:
        seq_latencies_ram.append(lat_us)
        seq_hit += 1
    else:
        seq_latencies_nvme.append(lat_us)
        seq_miss += 1

print(f"      RAM hits  : {seq_hit:>6}  avg={statistics.mean(seq_latencies_ram):.2f} µs"
      if seq_latencies_ram else "      RAM hits  :      0")
print(f"      NVMe reads: {seq_miss:>6}  avg={statistics.mean(seq_latencies_nvme):.2f} µs"
      if seq_latencies_nvme else "      NVMe reads:      0")
print()

# ── Phase 3: Re-read same pages — LRU should serve from RAM now ───────────────
print(f"[3/5] Re-reading same {min(OVERFLOW_PAGES, RAM_CACHE_PAGES*2)} pages (LRU warm-up)...")
rewarm_pages = min(OVERFLOW_PAGES, RAM_CACHE_PAGES * 2)
rewarm_lat_ram  = []
rewarm_lat_nvme = []

for i in range(rewarm_pages):
    t0 = time.perf_counter()
    engine.read_page(i % OVERFLOW_PAGES)
    lat_us = (time.perf_counter() - t0) * 1_000_000
    if lat_us < RAM_HIT_THRESH_US:
        rewarm_lat_ram.append(lat_us)
    else:
        rewarm_lat_nvme.append(lat_us)

rewarm_hit_rate = len(rewarm_lat_ram) / rewarm_pages * 100
print(f"      RAM hits  : {len(rewarm_lat_ram):>6} / {rewarm_pages}  "
      f"({rewarm_hit_rate:.1f}%)")
if rewarm_lat_ram:
    print(f"      RAM avg   : {statistics.mean(rewarm_lat_ram):.2f} µs")
if rewarm_lat_nvme:
    print(f"      NVMe avg  : {statistics.mean(rewarm_lat_nvme):.2f} µs")
print()

# ── Phase 4: Random workload — cache pressure ─────────────────────────────────
print(f"[4/5] Random read workload ({WORKLOAD_READS} reads, across {OVERFLOW_PAGES} pages) …")
random.seed(42)
rand_order = [random.randint(0, OVERFLOW_PAGES - 1) for _ in range(WORKLOAD_READS)]
rand_lat_ram  = []
rand_lat_nvme = []

for pid in rand_order:
    t0 = time.perf_counter()
    engine.read_page(pid)
    lat_us = (time.perf_counter() - t0) * 1_000_000
    if lat_us < RAM_HIT_THRESH_US:
        rand_lat_ram.append(lat_us)
    else:
        rand_lat_nvme.append(lat_us)

rand_hit_rate = len(rand_lat_ram) / WORKLOAD_READS * 100
print(f"      RAM hits  : {len(rand_lat_ram):>6} / {WORKLOAD_READS}  ({rand_hit_rate:.1f}%)")
print(f"      NVMe reads: {len(rand_lat_nvme):>6} / {WORKLOAD_READS}  "
      f"({100-rand_hit_rate:.1f}%)")
if rand_lat_ram:
    print(f"      RAM avg   : {statistics.mean(rand_lat_ram):.2f} µs")
if rand_lat_nvme:
    print(f"      NVMe avg  : {statistics.mean(rand_lat_nvme):.2f} µs")
print()

# ── Phase 5: Final metrics + verdict ─────────────────────────────────────────
print(f"[5/5] Final engine metrics …")
final = engine.get_metrics()
all_lats_ram  = seq_latencies_ram  + rewarm_lat_ram  + rand_lat_ram
all_lats_nvme = seq_latencies_nvme + rewarm_lat_nvme + rand_lat_nvme
total_reads   = len(all_lats_ram) + len(all_lats_nvme)
overall_hr    = len(all_lats_ram) / total_reads * 100 if total_reads else 0

effective_lat = (
    (overall_hr/100 * (statistics.mean(all_lats_ram)  if all_lats_ram  else 0)) +
    ((100-overall_hr)/100 * (statistics.mean(all_lats_nvme) if all_lats_nvme else 0))
)

engine.close()

print(f"\n{BAR}")
print(f"  FINAL RESULTS -- NVMe as Extended RAM")
print(f"{BAR}")
print(f"  Total reads measured  : {total_reads}")
print(f"  RAM cache hits        : {len(all_lats_ram):>6}  ({overall_hr:.2f}%)")
print(f"  NVMe SSD reads        : {len(all_lats_nvme):>6}  ({100-overall_hr:.2f}%)")
print()
if all_lats_ram:
    print(f"  RAM  avg latency      : {statistics.mean(all_lats_ram):.3f} µs")
    print(f"  RAM  p99 latency      : {statistics.quantiles(all_lats_ram, n=100)[-1]:.3f} µs")
if all_lats_nvme:
    print(f"  NVMe avg latency      : {statistics.mean(all_lats_nvme):.1f} µs")
    print(f"  NVMe p99 latency      : {statistics.quantiles(all_lats_nvme, n=100)[-1]:.1f} µs")
print(f"  Effective latency     : {effective_lat:.3f} µs  (weighted by hit rate)")
print(f"  SSD total writes      : {final['ssd_writes']}")
print(f"  SSD total reads       : {final['ssd_reads']}")
print(f"  Compression ratio     : {final['compression_ratio']:.2f}×")
print(f"  NVMe pool used        : {final['ssd_used_mb']:.1f} MB / {final['pool_size_gb']:.0f} GB")
print()

# Speedup: ratio of NVMe latency to effective latency
if all_lats_nvme and effective_lat > 0:
    speedup = statistics.mean(all_lats_nvme) / effective_lat
    print(f"  HyperRAM speedup vs pure NVMe: {speedup:.1f}×  "
          f"(prefetcher hides {overall_hr:.1f}% of NVMe latency)")
print()

print(f"  VERDICT: ", end="")
if final['ssd_writes'] > 0 and final['ssd_reads'] > 0:
    print("[PASS] NVMe SSD is actively serving as extended RAM.")
    print(f"       Pages overflow from RAM -> NVMe -> promoted back to RAM")
    print(f"       on access. The NVMe pool IS your extended memory tier.")
elif final['ssd_writes'] > 0:
    print("[PARTIAL] Pages written to NVMe but none read back yet.")
else:
    print("[FAIL] No NVMe I/O detected. RAM cache may be too large.")

print(f"\n{BAR}\n")
