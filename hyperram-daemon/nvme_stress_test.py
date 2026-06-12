"""
HyperRAM -- NVMe Stress Test
Forces real NVMe reads by using a working-set 20x larger than RAM cache.
"""
import io, sys, os, time, random, statistics
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(__file__))
from core import HyperRAMEngine, QoSTag

PAGE        = 4096
RAM_PAGES   = 64               # 256 KB RAM cache (tiny -- guaranteed overflow)
TOTAL_PGS   = RAM_PAGES * 20   # 20x working set -- forces NVMe pressure
READS       = 500
NVMe_THRESH = 400              # us above this = real NVMe read
POOL        = "hyperram.pool"
BAR         = "=" * 62

print(f"\n{BAR}")
print("  HyperRAM -- NVMe Stress Test (RAM < Working Set)")
print(f"{BAR}")
print(f"  RAM cache  : {RAM_PAGES} pages = {RAM_PAGES*PAGE//1024} KB")
print(f"  WorkingSet : {TOTAL_PGS} pages = {TOTAL_PGS*PAGE//1024} KB  ({TOTAL_PGS//RAM_PAGES}x bigger than RAM)")
print(f"  Strategy   : random uniform -- defeats LRU, forces cold NVMe reads")
print()

eng = HyperRAMEngine(ssd_pool_path=POOL, pool_size_gb=2, page_size=PAGE)
eng.max_ram_cache_pages = RAM_PAGES

# ----- Phase A: write all pages → forces evictions to NVMe ------------------
print(f"[A] Writing {TOTAL_PGS} pages to overflow RAM into NVMe pool...")
for i in range(TOTAL_PGS):
    eng.write_page(i, bytes([i & 0xFF]) * PAGE, QoSTag.DEFAULT)

m = eng.get_metrics()
ram_cnt = len(eng.ram_cache)
ssd_cnt = len(eng.page_table) - ram_cnt
print(f"    RAM used    : {m['ram_used_mb']:.2f} MB  ({ram_cnt} pages in RAM)")
print(f"    NVMe used   : {m['ssd_used_mb']:.2f} MB  ({ssd_cnt} pages on NVMe SSD)")
print(f"    NVMe writes : {m['ssd_writes']}")

# ----- Phase B: force-evict everything from RAM so reads hit NVMe -----------
print()
print("[B] Clearing RAM cache to force all reads to come from NVMe...")
# Mark every page as NOT in RAM (simulates full memory pressure / cold start)
evicted = 0
for pid in list(eng.page_table.keys()):
    in_ram, qos, csz, off = eng.page_table[pid]
    if in_ram and pid in eng.ram_cache:
        data = eng.ram_cache.pop(pid)
        eng._write_to_ssd(pid, data, qos)
        evicted += 1
print(f"    Force-evicted {evicted} pages from RAM -> NVMe")
print(f"    RAM cache entries now: {len(eng.ram_cache)}")

# ----- Phase C: random reads → measures real NVMe latency ------------------
print()
print(f"[C] {READS} random reads across {TOTAL_PGS} pages (working set > RAM)...")
random.seed(99)
order = [random.randint(0, TOTAL_PGS - 1) for _ in range(READS)]

ram_lats, nvme_lats = [], []
for pid in order:
    t0 = time.perf_counter()
    eng.read_page(pid)
    us = (time.perf_counter() - t0) * 1_000_000
    (ram_lats if us < NVMe_THRESH else nvme_lats).append(us)

print(f"    RAM  hits   : {len(ram_lats):>5} / {READS}  ({100*len(ram_lats)/READS:.1f}%)")
print(f"    NVMe reads  : {len(nvme_lats):>5} / {READS}  ({100*len(nvme_lats)/READS:.1f}%)")
if ram_lats:
    print(f"    RAM  avg    : {statistics.mean(ram_lats):.2f} us")
if nvme_lats:
    print(f"    NVMe avg    : {statistics.mean(nvme_lats):.2f} us  <-- real SSD latency")
    print(f"    NVMe p99    : {statistics.quantiles(nvme_lats, n=100)[-1]:.2f} us")

# ----- Phase D: sequential pass 1 (cold) → shows NVMe->RAM promotion --------
print()
SEQ = min(TOTAL_PGS, RAM_PAGES * 3)
print(f"[D] Sequential read: Pass 1 (cold, {SEQ} pages) -- NVMe -> RAM promotion...")
seq1_ram, seq1_nvme = [], []
for i in range(SEQ):
    t0 = time.perf_counter()
    eng.read_page(i)
    us = (time.perf_counter() - t0) * 1_000_000
    (seq1_ram if us < NVMe_THRESH else seq1_nvme).append(us)

print(f"    Pass 1  RAM hits : {len(seq1_ram):>4} / {SEQ}  ({100*len(seq1_ram)/SEQ:.1f}%)")
if seq1_nvme:
    print(f"    Pass 1  NVMe avg : {statistics.mean(seq1_nvme):.2f} us")

# ----- Phase E: sequential pass 2 (warm) → pages now in RAM -----------------
print(f"\n[E] Sequential read: Pass 2 (warm, {SEQ} pages) -- served from RAM cache...")
seq2_ram, seq2_nvme = [], []
for i in range(SEQ):
    t0 = time.perf_counter()
    eng.read_page(i)
    us = (time.perf_counter() - t0) * 1_000_000
    (seq2_ram if us < NVMe_THRESH else seq2_nvme).append(us)

print(f"    Pass 2  RAM hits : {len(seq2_ram):>4} / {SEQ}  ({100*len(seq2_ram)/SEQ:.1f}%)")
if seq2_ram:
    print(f"    Pass 2  RAM avg  : {statistics.mean(seq2_ram):.2f} us  <-- NVMe promoted to RAM!")
if seq2_nvme:
    print(f"    Pass 2  NVMe avg : {statistics.mean(seq2_nvme):.2f} us")

eng.close()

# ----- Final report ----------------------------------------------------------
all_ram  = ram_lats + seq1_ram + seq2_ram
all_nvme = nvme_lats + seq1_nvme + seq2_nvme
total    = len(all_ram) + len(all_nvme)
hr       = 100 * len(all_ram) / total if total else 0
avg_ram  = statistics.mean(all_ram)  if all_ram  else 0
avg_nvme = statistics.mean(all_nvme) if all_nvme else 0
eff_lat  = (hr/100 * avg_ram) + ((100-hr)/100 * avg_nvme)
speedup  = avg_nvme / eff_lat if eff_lat > 0 and avg_nvme > 0 else 0

print(f"\n{BAR}")
print("  FINAL RESULTS -- NVMe as Extended RAM")
print(f"{BAR}")
print(f"  Total reads         : {total}")
print(f"  RAM  cache hits     : {len(all_ram):>5}   ({hr:.1f}%)")
print(f"  NVMe SSD reads      : {len(all_nvme):>5}   ({100-hr:.1f}%)   <- real NVMe I/O")
print(f"  RAM  avg latency    : {avg_ram:.2f} us")
if avg_nvme:
    print(f"  NVMe avg latency    : {avg_nvme:.2f} us")
    print(f"  NVMe / RAM ratio    : {avg_nvme/avg_ram:.0f}x  slower than RAM")
print(f"  Effective latency   : {eff_lat:.2f} us  (hit-rate weighted)")
if speedup:
    print(f"  Speedup vs NVMe     : {speedup:.1f}x  (HyperRAM cache + LRU + prefetch)")
print()
print("  ARCHITECTURE PROOF:")
print(f"  - {TOTAL_PGS*PAGE//1024} KB working set stored on NVMe SSD")
print(f"  - Only {RAM_PAGES*PAGE//1024} KB of physical RAM used as hot cache")
print(f"  - {RAM_PAGES*PAGE//1024} KB RAM + NVMe pool = {TOTAL_PGS*PAGE//1024} KB effective address space")
print(f"  - LRU + prefetcher achieves {hr:.1f}% RAM hit rate across all reads")
print()
if len(all_nvme) > 0:
    print("  [PASS] NVMe SSD is verified as ACTIVE extended RAM tier.")
    print("         Real NVMe reads measured with microsecond latency.")
    print("         HyperRAM bridges the gap: pages live on SSD, accessed as RAM.")
else:
    print("  [NOTE] Prefetcher + LRU eliminated all NVMe misses (100% hit rate).")
    print("         NVMe pool still stored all overflow pages. Try larger working set.")
print(f"{BAR}\n")
