# -*- coding: utf-8 -*-
r"""
=============================================================================
  fig_architectural_comparison.py
  —  HyperRAM Most Important Result: Architectural Split Figure

  Produces ONE figure (Fig 6 for the paper) that captures the headline finding:

    The kernel driver and the userspace engine measure DIFFERENT things.
    Neither is "wrong" — they evaluate complementary claims.

  Figure layout (3 panels):
    Left   : Architecture diagram (what each path actually touches)
    Centre : Feature matrix (what each implementation provides)
    Right  : Latency breakdown (measured vs corrected estimate)

  Usage:
    venv\Scripts\python.exe fig_architectural_comparison.py
    venv\Scripts\python.exe fig_architectural_comparison.py --output-dir results/figures
    venv\Scripts\python.exe fig_architectural_comparison.py --csv results/kernel_vs_userspace_*.csv
=============================================================================
"""
import sys, os
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import argparse, glob, csv, time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
import numpy as np

# ---------------------------------------------------------------------------
# Style (matches plot_results.py palette)
# ---------------------------------------------------------------------------
BLUE   = "#2563EB"
GREEN  = "#16A34A"
ORANGE = "#EA580C"
RED    = "#DC2626"
GRAY   = "#6B7280"
TEAL   = "#0D9488"
AMBER  = "#D97706"
PURPLE = "#7C3AED"

plt.rcParams.update({
    "font.family":       "DejaVu Sans",
    "font.size":         10,
    "axes.titlesize":    11,
    "axes.labelsize":    10,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "figure.dpi":        150,
    "savefig.dpi":       200,
    "savefig.bbox":      "tight",
})

CHECK = "\u2713"   # ✓
CROSS = "\u26a0"   # ⚠
DASH  = "\u2014"   # —


# ---------------------------------------------------------------------------
# Panel A: Architecture diagram as a clean text-box flow
# ---------------------------------------------------------------------------
def draw_architecture(ax):
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    ax.set_title("(a)  What Each Path Measures", fontsize=11, fontweight="bold", pad=8)

    def box(x, y, w, h, text, color, fontsize=9, alpha=0.92, bold=False):
        rect = mpatches.FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.15",
            facecolor=color, edgecolor="white",
            linewidth=1.5, alpha=alpha, zorder=3
        )
        ax.add_patch(rect)
        fw = "bold" if bold else "normal"
        ax.text(x + w/2, y + h/2, text,
                ha="center", va="center",
                fontsize=fontsize, color="white",
                fontweight=fw, zorder=4,
                wrap=True)

    def arrow(x1, y1, x2, y2, color="#444"):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="->",
                                   color=color, lw=1.5),
                    zorder=2)

    def label(x, y, text, color=GRAY):
        ax.text(x, y, text, ha="center", va="center",
                fontsize=7.5, color=color,
                style="italic", zorder=5)

    # ── Left column: Userspace path ──────────────────────────────────────
    box(0.3, 8.2, 4.0, 0.9, "Application / Benchmark", BLUE, bold=True)
    arrow(2.3, 8.2, 2.3, 7.5, BLUE)
    box(0.3, 6.5, 4.0, 0.9, "core.py  (Python mmap)", BLUE)
    arrow(2.3, 6.5, 2.3, 5.8, BLUE)
    box(0.3, 4.8, 4.0, 0.9, "hyperram.pool  (file on NVMe)", GREEN, bold=True)
    arrow(2.3, 4.8, 2.3, 4.1, BLUE)
    box(0.3, 3.2, 4.0, 0.9, "Real NVMe SSD  (PCIe)", GREEN, bold=True)

    label(2.3, 7.4, "Python function call")
    label(2.3, 6.4, "mmap page fault / OS cache")
    label(2.3, 5.1, "OS disk I/O  (~100–2000 µs miss)")

    ax.text(2.3, 2.6, "USERSPACE PATH\nPrimary Evaluation",
            ha="center", va="center", fontsize=8.5,
            color=BLUE, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#EFF6FF", edgecolor=BLUE, alpha=0.8))

    # ── Right column: Kernel path ─────────────────────────────────────────
    box(5.7, 8.2, 4.0, 0.9, "Application / Benchmark", PURPLE, bold=True)
    arrow(7.7, 8.2, 7.7, 7.5, PURPLE)
    box(5.7, 6.5, 4.0, 0.9, r"kernel_client.py (IOCTL)", PURPLE)
    arrow(7.7, 6.5, 7.7, 5.8, PURPLE)
    box(5.7, 4.8, 4.0, 0.9, "HyperRAM.sys  (WDM driver)", PURPLE, bold=True)
    arrow(7.7, 4.8, 7.7, 4.1, PURPLE)
    box(5.7, 3.2, 4.0, 0.9, "NonPagedPool RAM  (~50 µs stall)", ORANGE, bold=True)

    label(7.7, 7.4, "ctypes DeviceIoControl")
    label(7.7, 6.4, "IRP_MJ_READ dispatch  (~5 µs)")
    label(7.7, 5.1, "KeStall(50) — simulated, not real NVMe")

    ax.text(7.7, 2.6, "KERNEL PATH\nSecondary Evaluation",
            ha="center", va="center", fontsize=8.5,
            color=PURPLE, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#F5F3FF", edgecolor=PURPLE, alpha=0.8))

    # ── Central divider ──────────────────────────────────────────────────
    ax.plot([5.0, 5.0], [2.0, 9.5], "--", color=GRAY, lw=1.0, alpha=0.5, zorder=1)
    ax.text(5.0, 1.4, "Different backing stores\n→ complementary claims",
            ha="center", va="center", fontsize=8, color=GRAY,
            style="italic")


# ---------------------------------------------------------------------------
# Panel B: Feature matrix
# ---------------------------------------------------------------------------
def draw_feature_matrix(ax):
    ax.axis("off")
    ax.set_title("(b)  Implementation Feature Matrix", fontsize=11, fontweight="bold", pad=8)

    features = [
        ("Real NVMe I/O",            True,  False),
        ("LZ4 Compression",          True,  False),  # kernel mock
        ("LRU Eviction",             True,  False),
        ("Multi-GB Pool",            True,  False),
        ("Checkpoint / Recovery",    True,  False),
        ("Write Amplification",      True,  False),
        ("Tau Predictor",            True,  True),
        ("Stride Prefetcher",        True,  True),
        ("Statistics Export",        True,  True),
        ("Driver Loads on Windows",  False, True),
        ("IOCTL Interface",          False, True),
        ("Zero-copy Kernel Access",  False, True),
        ("Kernel-mode Feasibility",  False, True),
    ]

    col_x   = [0.02, 0.56, 0.80]
    row_h   = 0.072
    y_start = 0.93

    # Header
    ax.text(col_x[0], y_start + 0.02, "Feature",
            transform=ax.transAxes, fontsize=9, fontweight="bold", color="#111")
    ax.text(col_x[1], y_start + 0.02, "Userspace",
            transform=ax.transAxes, fontsize=9, fontweight="bold", color=BLUE, ha="center")
    ax.text(col_x[2], y_start + 0.02, "Kernel",
            transform=ax.transAxes, fontsize=9, fontweight="bold", color=PURPLE, ha="center")

    # Divider line in axes coordinates
    line = plt.Line2D([0, 1], [y_start, y_start], transform=ax.transAxes,
                      color=GRAY, lw=0.8)
    ax.add_line(line)


    for i, (feat, us, kn) in enumerate(features):
        y = y_start - (i + 1) * row_h
        bg = "#F9FAFB" if i % 2 == 0 else "white"
        ax.add_patch(mpatches.FancyBboxPatch(
            (0, y - row_h * 0.5), 1, row_h,
            boxstyle="square,pad=0",
            transform=ax.transAxes,
            facecolor=bg, edgecolor="none", zorder=0))

        ax.text(col_x[0], y, feat,
                transform=ax.transAxes, fontsize=8.5, va="center", color="#222")

        # Userspace cell
        us_sym  = CHECK if us  else DASH
        us_col  = GREEN if us  else "#CCC"
        ax.text(col_x[1], y, us_sym,
                transform=ax.transAxes, fontsize=10, va="center",
                ha="center", color=us_col, fontweight="bold")

        # Kernel cell
        kn_sym  = CHECK if kn  else CROSS
        kn_col  = GREEN if kn  else AMBER
        ax.text(col_x[2], y, kn_sym,
                transform=ax.transAxes, fontsize=10, va="center",
                ha="center", color=kn_col, fontweight="bold")

    # Legend
    legend_y = y_start - (len(features) + 1) * row_h
    ax.text(0.02, legend_y, f"{CHECK} = Implemented   {CROSS} = Not yet   {DASH} = N/A",
            transform=ax.transAxes, fontsize=7.5, color=GRAY, style="italic")


# ---------------------------------------------------------------------------
# Panel C: Latency breakdown bar chart
# ---------------------------------------------------------------------------
def draw_latency_breakdown(ax, measured_kernel_us=386.5, measured_us_us=1.0):
    ax.set_title("(c)  RAM-Hit Latency Analysis", fontsize=11, fontweight="bold", pad=8)

    # Components stacked for the corrected kernel estimate
    components = {
        "IRP dispatch":      3.0,
        "Spin-lock acq":     0.5,
        "4 KB RtlCopyMemory":1.5,
        "IOCTL return":      3.0,
        "Python ctypes OH":  8.0,
    }
    est_total = sum(components.values())

    # Bars
    categories = ["Userspace\n(measured)", "Kernel\n(measured, \nWriteLog ON)",
                  "Kernel\n(estimated,\nWriteLog OFF)"]
    values     = [measured_us_us, measured_kernel_us, est_total]
    colors     = [BLUE, ORANGE, GREEN]

    bars = ax.bar(categories, values, color=colors, width=0.5,
                  edgecolor="white", linewidth=1.2, zorder=3)

    # Value labels
    for bar, val in zip(bars, values):
        label = f"{val:.0f} µs" if val >= 10 else f"{val:.1f} µs"
        ax.text(bar.get_x() + bar.get_width()/2,
                bar.get_height() + max(values) * 0.01,
                label, ha="center", va="bottom",
                fontsize=9, fontweight="bold", color="#222")

    # Stacked breakdown for the estimated bar
    est_bar = bars[2]
    bottom  = 0
    stack_colors = [TEAL, "#0D9488", "#047857", "#065F46", "#6B7280"]
    for (comp_name, comp_val), sc in zip(components.items(), stack_colors):
        ax.bar(est_bar.get_x() + est_bar.get_width()/2,
               comp_val, bottom=bottom, width=0.5,
               color=sc, edgecolor="white", linewidth=0.5,
               zorder=4, alpha=0.85)
        if comp_val >= 1.5:
            ax.text(est_bar.get_x() + est_bar.get_width()/2,
                    bottom + comp_val/2,
                    f"{comp_name}\n{comp_val:.1f} µs",
                    ha="center", va="center",
                    fontsize=6.5, color="white", zorder=5)
        bottom += comp_val

    # Annotation: WriteLog dominance
    ax.annotate(
        f"WriteLog()\n{measured_kernel_us - est_total:.0f} µs\nfile I/O overhead\n(ZwCreateFile×N)",
        xy=(1, measured_kernel_us),
        xytext=(1.55, measured_kernel_us * 0.7),
        fontsize=7.5, color=RED,
        arrowprops=dict(arrowstyle="->", color=RED, lw=1.2),
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#FEF2F2",
                  edgecolor=RED, alpha=0.9),
    )

    ax.set_ylabel("Latency (µs)", fontsize=10)
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
        lambda v, _: f"{v:.0f}" if v >= 1 else f"{v:.2f}"))
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    ax.set_axisbelow(True)

    # Reference lines
    ax.axhline(est_total, color=GREEN, linestyle="--", lw=1.2, alpha=0.7)
    ax.text(2.55, est_total * 1.05,
            f"Target: ~{est_total:.0f} µs\n(no logging)",
            fontsize=7, color=GREEN, va="bottom")


# ---------------------------------------------------------------------------
# Main figure assembly
# ---------------------------------------------------------------------------
def build_figure(output_dir, measured_kernel=386.5, measured_us=1.0):
    fig = plt.figure(figsize=(18, 8))
    fig.patch.set_facecolor("white")

    gs = fig.add_gridspec(1, 3, width_ratios=[2.0, 1.4, 1.2],
                          wspace=0.08, left=0.02, right=0.98,
                          top=0.88, bottom=0.10)

    ax_arch = fig.add_subplot(gs[0])
    ax_feat = fig.add_subplot(gs[1])
    ax_lat  = fig.add_subplot(gs[2])

    draw_architecture(ax_arch)
    draw_feature_matrix(ax_feat)
    draw_latency_breakdown(ax_lat, measured_kernel, measured_us)

    fig.suptitle(
        "Figure 6:  HyperRAM Architectural Split — Userspace vs Kernel-Mode Evaluation",
        fontsize=13, fontweight="bold", y=0.97, color="#111"
    )

    # Caption box
    caption = (
        "Key finding: the kernel driver (HyperRAM.sys) and the userspace engine (core.py) measure complementary properties. "
        "The userspace path evaluates real NVMe tiering, compression, and write amplification using an mmap-backed pool. "
        "The kernel path evaluates IRP dispatch feasibility, IOCTL interface overhead, and the tau-based predictor in ring-0. "
        "The 386 µs measured kernel RAM-hit latency is dominated by per-I/O file logging (WriteLog); "
        f"the corrected estimate without logging is ~{sum([3.0,0.5,1.5,3.0,8.0]):.0f} µs."
    )
    fig.text(0.5, 0.02, caption, ha="center", va="bottom",
             fontsize=8, color=GRAY, style="italic",
             wrap=True, ma="center",
             bbox=dict(boxstyle="round,pad=0.4", facecolor="#F9FAFB",
                       edgecolor="#E5E7EB", alpha=0.9))

    os.makedirs(output_dir, exist_ok=True)
    ts   = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"fig6_architectural_split_{ts}.png")
    fig.savefig(path)
    static_path = os.path.join(output_dir, "fig6_architectural_split.png")
    fig.savefig(static_path)
    plt.close(fig)
    print(f"  [plot] Fig 6 → {path}")
    print(f"  [plot] Fig 6 (static) → {static_path}")
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Generate Fig 6: HyperRAM Architectural Split")
    parser.add_argument("--output-dir", default=None,
                        help="Where to save the figure (default: ../results/figures/)")
    parser.add_argument("--csv", default=None,
                        help="kernel_vs_userspace CSV to read measured latencies from")
    parser.add_argument("--kernel-us",    type=float, default=None,
                        help="Override measured kernel RAM-hit latency (µs)")
    parser.add_argument("--userspace-us", type=float, default=None,
                        help="Override measured userspace RAM-hit latency (µs)")
    args = parser.parse_args()

    root        = os.path.dirname(__file__)
    output_dir  = args.output_dir or os.path.abspath(
                      os.path.join(root, "..", "results", "figures"))

    # Load measured latencies from CSV if provided
    kernel_us    = args.kernel_us    or 386.5
    userspace_us = args.userspace_us or 1.0

    if args.csv:
        csv_files = sorted(glob.glob(args.csv))
    else:
        # Auto-find latest kernel_vs_userspace CSV
        csv_files = sorted(glob.glob(
            os.path.join(root, "..", "results", "kernel_vs_userspace_*.csv")))

    if csv_files:
        latest = csv_files[-1]
        print(f"  [csv] Loading latency data from {os.path.basename(latest)}")
        with open(latest, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            path_label = row.get("path", "")
            ram_avg    = float(row.get("ram_avg_us", 0) or 0)
            if "Kernel" in path_label and "Fallback" not in path_label:
                if ram_avg > 0 and args.kernel_us is None:
                    kernel_us = ram_avg
                    print(f"  [csv] Kernel RAM avg: {kernel_us:.3f} µs  (from {path_label})")
            elif "Userspace" in path_label:
                if ram_avg > 0 and args.userspace_us is None:
                    userspace_us = ram_avg
                    print(f"  [csv] Userspace RAM avg: {userspace_us:.3f} µs  (from {path_label})")
    else:
        print("  [warn] No kernel_vs_userspace CSV found. Using default latency values.")

    print(f"\n  Kernel RAM-hit (measured):    {kernel_us:.1f} µs")
    print(f"  Userspace RAM-hit (measured): {userspace_us:.3f} µs")
    print(f"  WriteLog overhead estimate:   {kernel_us - 16.0:.0f} µs")
    print(f"  Corrected kernel estimate:    ~16 µs (no file logging)")

    path = build_figure(output_dir, kernel_us, userspace_us)

    print()
    print("  Paper text (copy/paste):")
    print("  " + "-"*66)
    print(r"  \subsection{Architectural Scope of Evaluation}")
    print()
    print("  Two complementary implementations were evaluated. The \\textbf{userspace")
    print("  engine} (\\texttt{core.py}) provides the primary quantitative evaluation:")
    print("  it backs all page operations with a real mmap-mapped NVMe pool file,")
    print("  exercises actual PCIe read/write latency during cache misses, and")
    print("  measures real write amplification, LRU eviction, and LZ4 compression.")
    print()
    print("  The \\textbf{kernel driver} (\\texttt{HyperRAM.sys}) provides a secondary")
    print("  feasibility evaluation: it demonstrates that the tau-based predictive")
    print("  prefetcher and stride detector can operate correctly inside ring-0,")
    print("  and characterises IRP dispatch and IOCTL round-trip overhead.")
    print("  Storage latency in the driver is currently modelled with a fixed")
    print(r"  50\,\textmu s busy-wait (\texttt{KeStallExecutionProcessor}); real")
    print("  NVMe evaluation is deferred to future work connecting the driver to")
    print("  the userspace pool via an asynchronous IRP chain.")
    print()
    print(f"  Measured kernel RAM-hit latency with per-I/O file logging enabled")
    print(f"  was {kernel_us:.0f}\\,\\textmu s; removing file logging is expected to")
    print(f"  reduce this to approximately 16\\,\\textmu s (IRP dispatch $\\approx$ 3\\,\\textmu s,")
    print(f"  spin-lock $\\approx$ 0.5\\,\\textmu s, \\texttt{{RtlCopyMemory}}(4\\,KB) $\\approx$")
    print(f"  1.5\\,\\textmu s, IOCTL return $\\approx$ 3\\,\\textmu s, ctypes overhead")
    print(f"  $\\approx$ 8\\,\\textmu s), compared to {userspace_us:.1f}\\,\\textmu s for the")
    print(f"  userspace path.")
    print("  " + "-"*66)
    print()
    print(f"  Figure saved: {path}")


if __name__ == "__main__":
    main()
