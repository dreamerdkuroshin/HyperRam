# HyperRAM Benchmark Runs — Raw Data Log

All CSV files in this directory are raw, unmodified outputs from `scale_benchmark.py`.
Every figure in the paper can be regenerated from these files alone.

## Run Conditions

| Timestamp | Repeats | WS pages | LOGICAL writes | Notes |
|-----------|---------|----------|----------------|-------|
| `20260608_012221` | 1 | 2048 (8 MB) | 2000 | First run; no `_std` columns (single pass) |
| `20260608_013903` | 3 | 2048 (8 MB) | 2000 | Fixed run; `_std` columns added via `--repeat 3` |
| `20260608_014454` | 3 | 2048 (8 MB) | 2000 | Third full run with `--repeat 3` |

## Working-Set Note (important for reviewers)

Runs `012221` – `014454` used **WS = 2048 pages = 8 MB**.  
Any cache ≥ 8 MB (all tested configs) holds the entire working set, which explains
the near-100% hit-rate at 128 MB / 64 MB / 32 MB / 16 MB caches. This is **expected and correct**,
not a measurement artifact. The interesting regime is `cache < WS`.

Future runs (WS = 8192 pages = 32 MB) will stress smaller caches meaningfully.

## Hardware

| Property | Value |
|----------|-------|
| OS | Windows 11 |
| Pool device | NVMe SSD (local, single drive) |
| Pool file | `../hyperram.pool` (10 GB) |
| Page size | 4096 bytes |
| Pool format | Memory-mapped file via `mmap` |

## Reproducing All Figures

```powershell
# From the repo root:

# Latest run only (default):
cd hyperram-daemon
venv\Scripts\python.exe plot_results.py

# Specific run:
venv\Scripts\python.exe plot_results.py --run-ts 20260608_013903

# Aggregate all 3 runs with mean±std error bars:
venv\Scripts\python.exe plot_results.py --aggregate

# One-command shortcut:
cd ..
reproduce.bat
```

## CSV Schema

### `*_memory_pressure.csv`
| Column | Description |
|--------|-------------|
| `cache_label` | Human-readable cache size label |
| `cache_mb` | Cache size in MB |
| `hit_rate_pct` | Mean hit rate (%) across repeats |
| `hit_rate_pct_std` | Std-dev of hit rate across repeats |
| `avg_us` | Mean read latency (µs) |
| `p99_us` | P99 read latency (µs) |
| `eff_us` | Effective latency (weighted by hit rate) |
| `speedup` | NVMe-avg / eff-avg speedup factor |

### `*_write_amp.csv`
| Column | Description |
|--------|-------------|
| `workload` | Workload pattern name |
| `cache_gb` | Cache size in GB |
| `logical_writes` | Number of logical page writes |
| `ssd_writes` | Actual SSD (pool) writes caused |
| `write_amp` | `ssd_writes / logical_writes` |
| `compress_ratio` | LZ4 compression ratio achieved |

### `*_summary.csv`
Scalar key→value pairs for all top-level benchmark metrics.
Each metric also has a `_std` variant from repeated runs.
