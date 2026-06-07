# -*- coding: utf-8 -*-
"""
=============================================================================
  plot_results.py  —  HyperRAM Paper Figure Generator
=============================================================================
  Reads CSVs produced by scale_benchmark.py and research_benchmark.py,
  then produces 4 publication-quality figures:

  Fig 1:  Hit Rate vs Cache Size            (cache pressure curve)
  Fig 2:  Effective Latency vs Hit Rate     (latency/hit-rate trade-off)
  Fig 3:  Write Amplification vs Workload   (SSD wear analysis)
  Fig 4:  Tail Latency CDF                  (P50–P99.9 for all mixes)

  Usage:
    venv/Scripts/python.exe plot_results.py                  # auto-detect latest CSVs
    venv/Scripts/python.exe plot_results.py --results-dir results/
    venv/Scripts/python.exe plot_results.py --live           # run benchmarks inline + plot
=============================================================================
"""
import sys, os
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import csv, glob, argparse, time, random, statistics, gc
import matplotlib
matplotlib.use("Agg")           # headless — no display required
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

# ── Style ──────────────────────────────────────────────────────────────────
COLORS = {
    "blue":   "#2563EB",
    "green":  "#16A34A",
    "orange": "#EA580C",
    "red":    "#DC2626",
    "purple": "#7C3AED",
    "gray":   "#6B7280",
    "teal":   "#0D9488",
}

plt.rcParams.update({
    "font.family":       "DejaVu Sans",
    "font.size":         11,
    "axes.titlesize":    13,
    "axes.labelsize":    11,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.grid":         True,
    "grid.alpha":        0.3,
    "grid.linestyle":    "--",
    "figure.dpi":        150,
    "savefig.dpi":       200,
    "savefig.bbox":      "tight",
})

PAGE_SIZE = 4096

# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------
def find_latest_csv(results_dir, suffix):
    pattern = os.path.join(results_dir, f"*{suffix}")
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def read_csv(path):
    if not path or not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def aggregate_csv_runs(results_dir, suffix):
    """
    Load ALL CSVs matching suffix in results_dir, group by first-column key,
    compute per-row mean ± std across runs.  Returns aggregated rows.
    Missing std columns in older runs are treated as 0.
    """
    pattern = os.path.join(results_dir, f"*{suffix}")
    files = sorted(glob.glob(pattern))
    if not files:
        return []
    if len(files) == 1:
        # Only one run — return as-is (no aggregation needed)
        return read_csv(files[0])

    print(f"  [aggregate] Found {len(files)} run(s) for {suffix}:")
    for f in files:
        print(f"    {os.path.basename(f)}")

    # Collect all data keyed by first-column value (e.g. cache_label or workload)
    from collections import defaultdict
    by_key = defaultdict(list)   # key → list of row dicts
    fieldnames = None
    key_col = None

    for fpath in files:
        rows = read_csv(fpath)
        if not rows:
            continue
        if fieldnames is None:
            fieldnames = list(rows[0].keys())
            key_col = fieldnames[0]
        for row in rows:
            by_key[row[key_col]].append(row)

    if not by_key:
        return []

    # Numeric columns to aggregate (skip key column and any existing _std columns)
    numeric_cols = [
        c for c in fieldnames
        if c != key_col and not c.endswith("_std")
    ]

    result_rows = []
    # Preserve original row order from first file
    first_file_rows = read_csv(files[0])
    ordered_keys = [r[key_col] for r in first_file_rows]

    for key in ordered_keys:
        rows_for_key = by_key.get(key, [])
        if not rows_for_key:
            continue
        out = {key_col: key}
        for col in numeric_cols:
            vals = []
            for r in rows_for_key:
                try:
                    vals.append(float(r[col]))
                except (KeyError, ValueError):
                    pass
            if vals:
                mean = sum(vals) / len(vals)
                std  = (sum((v - mean)**2 for v in vals) / max(1, len(vals) - 1)) ** 0.5 if len(vals) > 1 else 0.0
            else:
                mean, std = 0.0, 0.0
            out[col] = f"{mean:.6g}"
            out[f"{col}_std"] = f"{std:.6g}"
        result_rows.append(out)

    print(f"  [aggregate] Aggregated {len(files)} runs → {len(result_rows)} config rows")
    return result_rows


# ---------------------------------------------------------------------------
# Live data generation (runs minimal benchmarks inline)
# ---------------------------------------------------------------------------
def generate_live_data():
    """Run quick in-process benchmarks and return structured data."""
    from core import HyperRAMEngine, QoSTag
    POOL_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "hyperram.pool"))
    POOL_GB   = max(2, int(os.path.getsize(POOL_PATH) / (1024**3))) if os.path.exists(POOL_PATH) else 2

    def fresh_engine(ram_mb):
        eng = HyperRAMEngine(ssd_pool_path=POOL_PATH, pool_size_gb=POOL_GB, page_size=PAGE_SIZE)
        eng.max_ram_cache_pages = max(1, int(ram_mb * 1024 * 1024 / PAGE_SIZE))
        return eng

    def classify(lat, thresh=500.0): return lat < thresh

    # ── Fig 1 + 2 data: Memory Pressure ─────────────────────────────────────
    print("  [live] Running memory pressure benchmark...")
    WS_PAGES = 1024
    N_READS  = 1500
    CACHE_CONFIGS = [
        ("1024 MB", 1024), (" 512 MB", 512), (" 256 MB", 256),
        (" 128 MB", 128),  ("  64 MB",  64), ("  32 MB",  32), ("  16 MB",  16),
    ]
    CACHE_MB_VALUES = [1024, 512, 256, 128, 64, 32, 16]
    rng = random.Random(42)

    mp_data = []
    for i, (label, ram_mb) in enumerate(CACHE_CONFIGS):
        eng = fresh_engine(ram_mb)
        for j in range(WS_PAGES):
            eng.write_page(j, bytes([j & 0xFF]) * PAGE_SIZE)
        hot = WS_PAGES // 5
        lats_ram, lats_nvme = [], []
        for _ in range(N_READS):
            pid = rng.randint(0, hot-1) if rng.random() < 0.80 else rng.randint(0, WS_PAGES-1)
            t0  = time.perf_counter()
            eng.read_page(pid)
            lat = (time.perf_counter() - t0) * 1_000_000
            (lats_ram if classify(lat) else lats_nvme).append(lat)
        total = len(lats_ram) + len(lats_nvme)
        hr    = len(lats_ram) / max(1, total) * 100
        all_l = sorted(lats_ram + lats_nvme)
        n     = len(all_l)
        p99   = all_l[min(int(0.99 * n), n-1)] if n else 0
        ram_avg  = statistics.mean(lats_ram)  if lats_ram  else 0
        nvme_avg = statistics.mean(lats_nvme) if lats_nvme else 0
        eff  = (hr/100)*ram_avg + (1-hr/100)*nvme_avg
        sp   = nvme_avg / eff if eff > 0 and nvme_avg > 0 else float('inf')
        mp_data.append({
            "cache_label": label.strip(), "cache_mb": CACHE_MB_VALUES[i],
            "hit_rate_pct": hr, "p99_us": p99, "eff_us": eff, "speedup": sp,
            "avg_us": statistics.mean(all_l) if all_l else 0,
        })
        eng.close()
        gc.collect()

    # ── Fig 3 data: Write Amplification ─────────────────────────────────────
    print("  [live] Running write amplification benchmark...")
    LOGICAL = 500
    WA_CONFIGS = [
        ("Sequential",       list(range(LOGICAL))),
        ("Repeated hot 10%", [random.Random(i).randint(0, LOGICAL//10) for i in range(LOGICAL)]),
        ("Pure random",      [random.Random(i*7).randint(0, LOGICAL-1) for i in range(LOGICAL)]),
    ]
    CACHES_WA = [("1 GB", 1024), ("512 MB", 512), ("128 MB", 128)]
    wa_data = []
    for wl_name, pages in WA_CONFIGS:
        for cache_label, ram_mb in CACHES_WA:
            eng = fresh_engine(ram_mb)
            for k, pid in enumerate(pages):
                eng.write_page(pid, bytes([k & 0xFF]) * PAGE_SIZE, QoSTag.DEFAULT)
            m  = eng.get_metrics()
            wa = m['ssd_writes'] / max(1, LOGICAL)
            wa_data.append({
                "workload":   wl_name,
                "cache_gb":   f"{ram_mb/1024:.2f}",
                "logical":    LOGICAL,
                "ssd_writes": m['ssd_writes'],
                "write_amp":  wa,
                "compress_ratio": m['compression_ratio'],
            })
            eng.close()
            gc.collect()

    # ── Fig 4 data: Tail Latency by mix ─────────────────────────────────────
    print("  [live] Running tail latency benchmark...")
    RAM_MB   = 2
    WS_PAGES = 1024
    N_READS  = 3000
    MIXES = {
        "All-RAM (hot)":   lambda rng, ws: [rng.randint(0, ws//8)       for _ in range(N_READS)],
        "80/20 Zipf":      lambda rng, ws: [rng.randint(0, ws//5-1) if rng.random() < 0.80
                                             else rng.randint(0, ws-1)  for _ in range(N_READS)],
        "50/50 Warm/Cold": lambda rng, ws: [rng.randint(0, ws//2-1) if rng.random() < 0.50
                                             else rng.randint(ws//2, ws-1) for _ in range(N_READS)],
        "All-NVMe (cold)": lambda rng, ws: [rng.randint(ws//2, ws-1)   for _ in range(N_READS)],
    }
    tail_data = {}
    for mix_name, mix_fn in MIXES.items():
        eng = fresh_engine(RAM_MB)
        for j in range(WS_PAGES):
            eng.write_page(j, bytes([j & 0xFF]) * PAGE_SIZE)
        for j in range(WS_PAGES // 4):
            eng.read_page(j)
        rng2 = random.Random(55)
        order = mix_fn(rng2, WS_PAGES)
        lats  = []
        for pid in order:
            t0 = time.perf_counter()
            eng.read_page(pid)
            lats.append((time.perf_counter() - t0) * 1_000_000)
        tail_data[mix_name] = sorted(lats)
        eng.close()
        gc.collect()

    return mp_data, wa_data, tail_data


# ---------------------------------------------------------------------------
# Figure 1: Hit Rate vs Cache Size
# ---------------------------------------------------------------------------
def plot_hit_rate_vs_cache(mp_data, output_path):
    fig, ax = plt.subplots(figsize=(7, 4.5))

    cache_mb = [float(r["cache_mb"]) for r in mp_data]
    hit_rate = [float(r["hit_rate_pct"]) for r in mp_data]
    yerr     = [float(r.get("hit_rate_pct_std", 0.0)) for r in mp_data]

    if any(e > 0.005 for e in yerr):
        ax.errorbar(cache_mb, hit_rate, yerr=yerr, fmt="o-", color=COLORS["blue"], linewidth=2,
                    markersize=7, markerfacecolor="white", markeredgewidth=2, capsize=4, elinewidth=1.5,
                    ecolor=COLORS["gray"], label="HyperRAM (Zipf 80/20)")
    else:
        ax.plot(cache_mb, hit_rate, "o-", color=COLORS["blue"], linewidth=2,
                markersize=7, markerfacecolor="white", markeredgewidth=2, label="HyperRAM (Zipf 80/20)")

    # Shade "good" zone
    ax.axhline(95, color=COLORS["green"], linestyle="--", linewidth=1.2, alpha=0.6, label="95% target")
    ax.axhline(90, color=COLORS["orange"], linestyle=":", linewidth=1.2, alpha=0.6, label="90% floor")
    ax.fill_between(cache_mb, 90, 100, alpha=0.05, color=COLORS["green"])

    ax.set_xscale("log", base=2)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(
        lambda v, _: f"{int(v)} MB" if v < 1024 else f"{int(v)//1024} GB"))
    ax.set_xlabel("RAM Cache Size")
    ax.set_ylabel("Cache Hit Rate (%)")
    ax.set_title("Fig 1 — Hit Rate vs Cache Size\n(Zipf 80/20 workload, 4 MB working set)")
    ax.set_ylim(70, 102)
    ax.legend(loc="lower right", fontsize=9)
    ax.invert_xaxis()
    plt.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    print(f"  [plot] Fig 1 → {output_path}")


# ---------------------------------------------------------------------------
# Figure 2: Effective Latency vs Hit Rate
# ---------------------------------------------------------------------------
def plot_latency_vs_hit_rate(mp_data, output_path):
    fig, ax = plt.subplots(figsize=(7, 4.5))

    hit_rate = [float(r["hit_rate_pct"]) for r in mp_data]
    eff_us   = [float(r["eff_us"]) for r in mp_data]
    labels   = [r["cache_label"] for r in mp_data]

    sc = ax.scatter(hit_rate, eff_us, c=[float(r["cache_mb"]) for r in mp_data],
                    cmap="Blues_r", s=80, zorder=5, edgecolors="white", linewidths=1)
    ax.plot(hit_rate, eff_us, "-", color=COLORS["blue"], linewidth=1.5, alpha=0.5)

    for i, (x, y, lbl) in enumerate(zip(hit_rate, eff_us, labels)):
        ax.annotate(lbl, (x, y), textcoords="offset points", xytext=(4, 4), fontsize=8,
                    color=COLORS["gray"])

    cbar = fig.colorbar(sc, ax=ax)
    cbar.set_label("Cache Size (MB)", fontsize=9)

    ax.set_xlabel("Cache Hit Rate (%)")
    ax.set_ylabel("Effective Read Latency (µs)")
    ax.set_title("Fig 2 — Effective Latency vs Hit Rate\n(lower-left is better)")
    plt.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    print(f"  [plot] Fig 2 → {output_path}")


# ---------------------------------------------------------------------------
# Figure 3: Write Amplification vs Workload
# ---------------------------------------------------------------------------
def plot_write_amplification(wa_data, output_path):
    workloads  = ["Sequential", "Repeated hot 10%", "Pure random"]
    cache_labels_map = {"1.00": "1 GB cache", "0.50": "512 MB cache", "0.13": "128 MB cache"}
    cache_colors     = {"1.00": COLORS["blue"], "0.50": COLORS["teal"], "0.13": COLORS["orange"]}

    # Build wa_map keyed by (workload, rounded cache_gb string)
    wa_map = {}
    wa_std_map = {}
    for r in wa_data:
        cgb = float(r["cache_gb"]) if isinstance(r["cache_gb"], str) else r["cache_gb"]
        # Round to nearest recognizable key
        if   cgb >= 0.9:  ck = "1.00"
        elif cgb >= 0.4:  ck = "0.50"
        else:             ck = "0.13"
        wa_map[(r["workload"], ck)] = float(r["write_amp"])
        wa_std_map[(r["workload"], ck)] = float(r.get("write_amp_std", 0.0))

    # Determine cache keys present in data
    all_cks  = sorted({k[1] for k in wa_map}, reverse=True)
    if not all_cks:
        print(f"  [skip] Fig 3 — empty write amp data")
        return

    x     = np.arange(len(workloads))
    width = 0.8 / max(len(all_cks), 1)

    fig, ax = plt.subplots(figsize=(8, 4.5))

    any_bar = False
    for i, ck in enumerate(all_cks):
        col   = cache_colors.get(ck, COLORS["gray"])
        label = cache_labels_map.get(ck, f"{ck} GB cache")
        vals  = [wa_map.get((wl, ck), 0.0) for wl in workloads]
        errs  = [wa_std_map.get((wl, ck), 0.0) for wl in workloads]
        offset = (i - len(all_cks)/2 + 0.5) * width
        
        yerr = errs if any(e > 0.005 for e in errs) else None
        bars  = ax.bar(x + offset, vals, width * 0.9, yerr=yerr, label=label,
                       color=col, alpha=0.85, zorder=3, capsize=3, 
                       error_kw=dict(elinewidth=1.2, ecolor=COLORS["gray"]))
        for bar, v in zip(bars, vals):
            if v > 0.001:
                any_bar = True
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.005,
                        f"{v:.2f}×", ha="center", va="bottom", fontsize=8)

    ax.axhline(1.0, color=COLORS["red"], linestyle="--", linewidth=1.2,
               alpha=0.7, label="WA = 1.0× baseline")
    ax.set_xticks(x)
    ax.set_xticklabels(workloads)
    ax.set_ylabel("Write Amplification (×)")
    ax.set_title("Fig 3 — Write Amplification vs Workload\n"
                 "(lower is better; WA < 1.0× = compression wins)")
    ax.legend(fontsize=9)
    max_wa = max((float(r["write_amp"]) for r in wa_data), default=1.0)
    ax.set_ylim(0, max(1.5, max_wa + 0.2))
    if not any_bar:
        ax.text(0.5, 0.5, "All WA = 0.0×\n(cache absorbs all writes;\nno evictions during test)",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=11, color=COLORS["gray"],
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#f0f0f0", alpha=0.8))
    plt.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    print(f"  [plot] Fig 3 → {output_path}")


# ---------------------------------------------------------------------------
# Figure 4: Tail Latency CDF
# ---------------------------------------------------------------------------
def plot_tail_latency_cdf(tail_data, output_path):
    """CDF of read latency for each workload mix — log-x axis."""
    mix_colors = {
        "All-RAM (hot)":   COLORS["green"],
        "80/20 Zipf":      COLORS["blue"],
        "50/50 Warm/Cold": COLORS["orange"],
        "All-NVMe (cold)": COLORS["red"],
    }

    fig, ax = plt.subplots(figsize=(9, 5.5))
    fig.subplots_adjust(left=0.12, right=0.97)

    pct_marks = [50, 90, 95, 99, 99.9]

    for mix_name, lats in tail_data.items():
        color = mix_colors.get(mix_name, COLORS["gray"])
        n     = len(lats)
        if n == 0:
            continue
        sorted_lats = sorted(lats)
        cdf = [(i + 1) / n * 100 for i in range(n)]
        ax.plot(sorted_lats, cdf, linewidth=2, label=mix_name, color=color, alpha=0.9)

    # Percentile grid lines — labels on the right side of y-axis
    for p in pct_marks:
        ax.axhline(p, color=COLORS["gray"], linestyle=":", linewidth=0.8, alpha=0.5)
        ax.annotate(f"P{p}", xy=(1, p), xycoords=("axes fraction", "data"),
                    xytext=(4, 0), textcoords="offset points",
                    fontsize=7.5, color=COLORS["gray"], va="center")

    ax.set_xscale("log")
    ax.set_xlabel("Read Latency (µs)  [log scale]")
    ax.set_ylabel("Percentile (%)")
    ax.set_title("Fig 4 — Tail Latency CDF by Workload Mix\n(HyperRAM  ·  P50 → P99.9)")
    ax.set_ylim(0, 103)
    ax.legend(loc="lower right", fontsize=9)
    plt.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    print(f"  [plot] Fig 4 → {output_path}")


# ---------------------------------------------------------------------------
# Figure 5 (bonus): Throughput bar chart
# ---------------------------------------------------------------------------
def plot_throughput_summary(summary, output_path):
    if not summary:
        return
    metrics = {}
    # Try parsing summary dict or list of {metric, value} rows
    if isinstance(summary, list):
        for r in summary:
            try:
                metrics[r["metric"]] = float(r["value"])
            except (KeyError, ValueError):
                pass
    elif isinstance(summary, dict):
        metrics = {k: float(v) for k, v in summary.items()
                   if v not in ("", "nan", "inf") and v == v}

    bars_data = [
        ("Fill\nthroughput",    metrics.get("fill_mb_s", 0),        metrics.get("fill_mb_s_std", 0),  "MB/s",  COLORS["blue"]),
        ("MT read\nthroughput", metrics.get("mt_tput_mb", 0),       metrics.get("mt_tput_mb_std", 0), "MB/s",  COLORS["teal"]),
        ("Checkpoint\nrecovery",metrics.get("with_ckpt_recovery",0)*100, metrics.get("with_ckpt_recovery_std",0)*100, "% pages", COLORS["green"]),
    ]

    labels = [b[0] for b in bars_data]
    values = [b[1] for b in bars_data]
    yerr   = [b[2] if b[2] > 0.05 else 0.0 for b in bars_data]
    units  = [b[3] for b in bars_data]
    colors = [b[4] for b in bars_data]

    if not any(v > 0 for v in values):
        return

    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.bar(labels, values, yerr=yerr, color=colors, alpha=0.85, zorder=3, capsize=4,
                   error_kw=dict(elinewidth=1.2, ecolor="#444"))
    for bar, v, u in zip(bars, values, units):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(values)*0.01,
                f"{v:.0f} {u}", ha="center", va="bottom", fontsize=9, fontweight="bold")

    ax.set_ylabel("Value")
    ax.set_title("Fig 5 — System Performance Overview\n(Stage-1 benchmark)")
    plt.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    print(f"  [plot] Fig 5 → {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="HyperRAM Paper Figure Generator")
    parser.add_argument("--results-dir", default=os.path.join(
                            os.path.dirname(__file__), "..", "results"),
                        help="Directory containing CSV files from scale_benchmark.py")
    parser.add_argument("--output-dir",  default=None,
                        help="Where to save figures (default: results/figures/)")
    parser.add_argument("--live",        action="store_true",
                        help="Run quick inline benchmarks instead of reading CSVs")
    parser.add_argument("--aggregate",   action="store_true",
                        help="Average ALL matching CSVs in results-dir with mean\u00b1std error bars")
    parser.add_argument("--run-ts",      default=None,
                        help="Pin to a specific run by timestamp prefix, e.g. 20260608_013903")
    args = parser.parse_args()

    results_dir = os.path.abspath(args.results_dir)
    output_dir  = os.path.abspath(args.output_dir) if args.output_dir else \
                  os.path.join(results_dir, "figures")
    os.makedirs(output_dir, exist_ok=True)

    print(f"\n  HyperRAM Figure Generator")
    print(f"  Results dir  : {results_dir}")
    print(f"  Output dir   : {output_dir}")
    mode_str = "LIVE" if args.live else ("AGGREGATE (mean\u00b1std)" if args.aggregate else "CSV (latest)")
    if args.run_ts:
        mode_str += f" [run-ts={args.run_ts}]"
    print(f"  Mode         : {mode_str}")
    print()

    mp_data   = []
    wa_data   = []
    tail_data = {}
    summary   = {}

    if args.live:
        mp_data, wa_data, tail_data = generate_live_data()
    else:
        # Helper: find CSV, optionally filtered by run timestamp
        def _find(suffix):
            if args.run_ts:
                # Glob for files containing the specific timestamp
                pattern = os.path.join(results_dir, f"*{args.run_ts}*{suffix}")
                files = sorted(glob.glob(pattern))
                return files[-1] if files else None
            if args.aggregate:
                return None   # handled below via aggregate_csv_runs
            return find_latest_csv(results_dir, suffix)

        if args.aggregate:
            # --- Aggregate mode: load ALL matching CSVs and compute mean±std ---
            mp_rows = aggregate_csv_runs(results_dir, "_memory_pressure.csv")
            wa_rows = aggregate_csv_runs(results_dir, "_write_amp.csv")
            sum_rows = aggregate_csv_runs(results_dir, "_summary.csv")
        else:
            mp_file  = _find("_memory_pressure.csv")
            wa_file  = _find("_write_amp.csv")
            sum_file = _find("_summary.csv")
            mp_rows  = read_csv(mp_file)  if mp_file  else []
            wa_rows  = read_csv(wa_file)  if wa_file  else []
            sum_rows = read_csv(sum_file) if sum_file else []
            if mp_file:  print(f"  [csv] Memory pressure: {mp_file}")
            if wa_file:  print(f"  [csv] Write amp:       {wa_file}")
            if sum_file: print(f"  [csv] Summary:         {sum_file}")

        if mp_rows:
            mp_data = [{
                "cache_label":   r["cache_label"],
                "cache_mb":      float(r["cache_mb"]),
                "hit_rate_pct":  float(r["hit_rate_pct"]),
                "hit_rate_pct_std": float(r.get("hit_rate_pct_std", 0.0)),
                "avg_us":        float(r["avg_us"]),
                "avg_us_std":    float(r.get("avg_us_std", 0.0)),
                "p99_us":        float(r["p99_us"]),
                "p99_us_std":    float(r.get("p99_us_std", 0.0)),
                "eff_us":        float(r["eff_us"]),
                "eff_us_std":    float(r.get("eff_us_std", 0.0)),
                "speedup":       float(r["speedup"]) if r["speedup"] not in ("inf", "nan") else 0,
                "speedup_std":   float(r.get("speedup_std", 0.0)) if r.get("speedup_std", "") not in ("inf", "nan") else 0,
            } for r in mp_rows]
        else:
            print("  [warn] No memory_pressure CSV found. Using --live mode for this plot.")
            mp_data, wa_data_live, tail_data_live = generate_live_data()
            if not wa_rows:  wa_rows = wa_data_live  # type: ignore
            tail_data = tail_data_live

        if wa_rows:
            wa_data = wa_rows

        if sum_rows:
            summary = {r["metric"]: r["value"] for r in sum_rows
                       if "metric" in r and "value" in r}

        # Tail latency: re-run live (always fast, <5 s)
        if not tail_data:
            print("  [live] Re-running tail latency (no CSV format for CDF)...")
            _, _, tail_data = generate_live_data()

    ts = time.strftime("%Y%m%d_%H%M%S")

    # ── Generate figures ──────────────────────────────────────────────────
    print()
    if mp_data:
        plot_hit_rate_vs_cache(mp_data,
            os.path.join(output_dir, f"fig1_hit_rate_vs_cache_{ts}.png"))
        plot_latency_vs_hit_rate(mp_data,
            os.path.join(output_dir, f"fig2_latency_vs_hit_rate_{ts}.png"))
    else:
        print("  [skip] Fig 1/2 — no memory pressure data")

    if wa_data:
        plot_write_amplification(wa_data,
            os.path.join(output_dir, f"fig3_write_amplification_{ts}.png"))
    else:
        print("  [skip] Fig 3 — no write amplification data")

    if tail_data:
        plot_tail_latency_cdf(tail_data,
            os.path.join(output_dir, f"fig4_tail_latency_cdf_{ts}.png"))
    else:
        print("  [skip] Fig 4 — no tail latency data")

    if summary:
        plot_throughput_summary(summary,
            os.path.join(output_dir, f"fig5_throughput_summary_{ts}.png"))

    print(f"\n  All figures saved to: {output_dir}")
    print(f"  Embed in paper with:  \\includegraphics{{figures/figN_...png}}")


if __name__ == "__main__":
    main()
