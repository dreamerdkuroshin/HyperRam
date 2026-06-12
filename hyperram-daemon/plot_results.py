# HyperRAM Visual Graph Generator

Generates publication-ready graphs from benchmark results.

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
============================================================================
  plot_results.py — HyperRAM Paper-Quality Graph Generator
============================================================================
  Creates matplotlib/seaborn graphs from benchmark CSV files:
    - Scalability curve (threads vs throughput)
    - Hit-rate sensitivity curve
    - Tail latency CDF
    - AI model comparison bar chart
    - Power efficiency scatter plot
    - Memory pressure curve
    - Write amplification comparison

  Output: PNG files (300 DPI) ready for paper inclusion

  Usage:
    python plot_results.py                      # All graphs
    python plot_results.py --scalability        # Just scalability
    python plot_results.py --ai-comparison      # Just AI models
    python plot_results.py --output figures/    # Custom output dir
============================================================================
"""
import sys, os
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
from pathlib import Path
import argparse

# Use non-interactive backend
matplotlib.use('Agg')

# Set style for publication-quality graphs
plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams['figure.figsize'] = (10, 6)
plt.rcParams['font.size'] = 12
plt.rcParams['axes.labelsize'] = 14
plt.rcParams['axes.titlesize'] = 16
plt.rcParams['xtick.labelsize'] = 12
plt.rcParams['ytick.labelsize'] = 12
plt.rcParams['legend.fontsize'] = 11
plt.rcParams['figure.dpi'] = 300

SEP = "=" * 72


class HyperRAMPlotter:
    """Generates all graphs for HyperRAM paper."""
    
    def __init__(self, results_dir='results', output_dir='figures'):
        self.results_dir = Path(results_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)
        
    def find_latest_csv(self, pattern):
        """Find most recent CSV matching pattern."""
        files = list(self.results_dir.glob(f'**/{pattern}'))
        if not files:
            return None
        return max(files, key=lambda p: p.stat().st_mtime)
    
    def plot_scalability(self):
        """Generate scalability curve (threads vs throughput)."""
        print("\n  Generating scalability curve...")
        
        csv_file = self.find_latest_csv('multithread_benchmark_*.csv')
        if not csv_file:
            print(f"  [WARN] No multithread benchmark CSV found")
            return None
        
        df = pd.read_csv(csv_file)
        
        fig, ax = plt.subplots(figsize=(10, 6))
        
        for label in df['label'].unique():
            subset = df[df['label'] == label]
            ax.plot(subset['threads'], subset['throughput_ops_sec'], 
                   marker='o', linewidth=2, markersize=8, label=label)
        
        ax.set_xlabel('Threads')
        ax.set_ylabel('Throughput (ops/sec)')
        ax.set_title('HyperRAM Scalability: Throughput vs Thread Count')
        ax.legend(loc='upper left')
        ax.grid(True, alpha=0.3)
        ax.set_xscale('log', base=2)
        
        # Add ideal scaling line
        max_threads = df['threads'].max()
        single_thread = df[df['threads'] == 1]['throughput_ops_sec'].values
        if len(single_thread) > 0:
            ideal = [single_thread[0] * t for t in [1, 4, 8, 16, 64] if t <= max_threads]
            ax.plot([1, 4, 8, 16, 64][:len(ideal)], ideal, 
                   'k--', alpha=0.5, label='Ideal Linear Scaling', linewidth=1.5)
        
        plt.tight_layout()
        output_path = self.output_dir / 'scalability_curve.png'
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"  ✓ Saved: {output_path}")
        return output_path
    
    def plot_hit_rate_sensitivity(self):
        """Generate hit-rate sensitivity curve."""
        print("\n  Generating hit-rate sensitivity curve...")
        
        csv_file = self.find_latest_csv('*memory_pressure*.csv')
        if not csv_file:
            print(f"  [WARN] No memory pressure CSV found")
            return None
        
        df = pd.read_csv(csv_file)
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
        
        # Left: Effective latency vs hit rate
        if 'hr' in df.columns and 'eff' in df.columns:
            ax1.plot(df['cache'], df['eff'], marker='o', linewidth=2, color='blue')
            ax1.set_xlabel('Cache Size')
            ax1.set_ylabel('Effective Latency (µs)')
            ax1.set_title('Effective Latency vs Cache Size')
            ax1.grid(True, alpha=0.3)
            ax1.tick_params(axis='x', rotation=45)
        
        # Right: Hit rate vs cache size
        if 'cache' in df.columns and 'hr' in df.columns:
            ax2.plot(df['cache'], df['hr'], marker='s', linewidth=2, color='green')
            ax2.set_xlabel('Cache Size')
            ax2.set_ylabel('Hit Rate (%)')
            ax2.set_title('Cache Hit Rate vs Cache Size')
            ax2.grid(True, alpha=0.3)
            ax2.tick_params(axis='x', rotation=45)
        
        plt.tight_layout()
        output_path = self.output_dir / 'hit_rate_sensitivity.png'
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"  ✓ Saved: {output_path}")
        return output_path
    
    def plot_tail_latency_cdf(self):
        """Generate tail latency CDF plot."""
        print("\n  Generating tail latency CDF...")
        
        # This would require detailed latency data
        # For now, create placeholder
        print(f"  [INFO] Tail latency CDF requires detailed latency samples")
        print(f"  [INFO] Skipping (can be added with per-op latency logging)")
        return None
    
    def plot_ai_model_comparison(self):
        """Generate AI model comparison bar chart."""
        print("\n  Generating AI model comparison...")
        
        # Try Ollama benchmark first
        csv_file = self.find_latest_csv('ollama_benchmark_*.csv')
        
        if not csv_file:
            # Fall back to simulated AI benchmark
            csv_file = self.find_latest_csv('ai_benchmark_*.csv')
        
        if not csv_file:
            print(f"  [WARN] No AI benchmark CSV found")
            return None
        
        df = pd.read_csv(csv_file)
        
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        
        # Top-left: Tokens/sec
        if 'tokens_per_sec' in df.columns:
            ax = axes[0, 0]
            models = df['model'].tolist()
            tokens = df['tokens_per_sec'].tolist()
            ax.barh(models, tokens, color='steelblue')
            ax.set_xlabel('Tokens/sec')
            ax.set_title('Inference Speed by Model')
            ax.grid(True, alpha=0.3, axis='x')
        
        # Top-right: Cache Hit Rate
        if 'hit_rate_pct' in df.columns:
            ax = axes[0, 1]
            models = df['model'].tolist()
            hit_rates = df['hit_rate_pct'].tolist()
            ax.barh(models, hit_rates, color='forestgreen')
            ax.set_xlabel('Cache Hit Rate (%)')
            ax.set_title('HyperRAM Cache Efficiency')
            ax.grid(True, alpha=0.3, axis='x')
        
        # Bottom-left: SSD Reads
        if 'ssd_reads' in df.columns:
            ax = axes[1, 0]
            models = df['model'].tolist()
            ssd_reads = df['ssd_reads'].tolist()
            ax.barh(models, ssd_reads, color='coral')
            ax.set_xlabel('SSD Reads')
            ax.set_title('NVMe Access During Inference')
            ax.grid(True, alpha=0.3, axis='x')
        
        # Bottom-right: Compression Ratio
        if 'compression_ratio' in df.columns:
            ax = axes[1, 1]
            models = df['model'].tolist()
            compression = df['compression_ratio'].tolist()
            ax.barh(models, compression, color='purple')
            ax.set_xlabel('Compression Ratio (×)')
            ax.set_title('Data Compression Effectiveness')
            ax.grid(True, alpha=0.3, axis='x')
        
        plt.tight_layout()
        output_path = self.output_dir / 'ai_model_comparison.png'
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"  ✓ Saved: {output_path}")
        return output_path
    
    def plot_power_efficiency(self):
        """Generate power efficiency scatter plot."""
        print("\n  Generating power efficiency scatter plot...")
        
        csv_file = self.find_latest_csv('power_benchmark_*.csv')
        if not csv_file:
            print(f"  [WARN] No power benchmark CSV found")
            return None
        
        df = pd.read_csv(csv_file)
        
        fig, ax = plt.subplots(figsize=(10, 6))
        
        if 'ops_per_joule' in df.columns and 'avg_latency_us' in df.columns:
            scatter = ax.scatter(df['ops_per_joule'], df['avg_latency_us'], 
                                s=100, alpha=0.7, c=range(len(df)), cmap='viridis')
            
            for i, row in df.iterrows():
                ax.annotate(row['label'], 
                           (row['ops_per_joule'], row['avg_latency_us']),
                           xytext=(5, 5), textcoords='offset points')
            
            ax.set_xlabel('Operations per Joule')
            ax.set_ylabel('Average Latency (µs)')
            ax.set_title('Power Efficiency vs Latency')
            ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        output_path = self.output_dir / 'power_efficiency.png'
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"  ✓ Saved: {output_path}")
        return output_path
    
    def plot_write_amplification(self):
        """Generate write amplification comparison."""
        print("\n  Generating write amplification comparison...")
        
        csv_file = self.find_latest_csv('*write_amp*.csv')
        if not csv_file:
            print(f"  [WARN] No write amplification CSV found")
            return None
        
        df = pd.read_csv(csv_file)
        
        fig, ax = plt.subplots(figsize=(10, 6))
        
        # Group by workload and cache size
        if all(col in df.columns for col in ['workload', 'cache', 'write_amp']):
            workloads = df['workload'].unique()
            x = range(len(workloads))
            width = 0.25
            
            for cache_size in df['cache'].unique():
                subset = df[df['cache'] == cache_size]
                values = [subset[subset['workload'] == w]['write_amp'].values[0] 
                         if len(subset[subset['workload'] == w]) > 0 else 0 
                         for w in workloads]
                ax.bar([i + width * list(df['cache'].unique()).index(cache_size) 
                       for i in x], values, width, label=f'{cache_size} RAM')
            
            ax.set_xlabel('Workload Type')
            ax.set_ylabel('Write Amplification (×)')
            ax.set_title('SSD Write Amplification by Cache Size and Workload')
            ax.set_xticks([i + width for i in x])
            ax.set_xticklabels(workloads, rotation=45)
            ax.legend()
            ax.grid(True, alpha=0.3, axis='y')
        
        plt.tight_layout()
        output_path = self.output_dir / 'write_amplification.png'
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"  ✓ Saved: {output_path}")
        return output_path
    
    def plot_memory_pressure(self):
        """Generate memory pressure curve."""
        print("\n  Generating memory pressure curve...")
        
        csv_file = self.find_latest_csv('*memory_pressure*.csv')
        if not csv_file:
            print(f"  [WARN] No memory pressure CSV found")
            return None
        
        df = pd.read_csv(csv_file)
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
        
        if all(col in df.columns for col in ['cache', 'hr', 'eff']):
            # Left: Hit rate vs cache
            ax1.plot(df['cache'], df['hr'], marker='o', linewidth=2, color='blue')
            ax1.set_xlabel('Cache Size')
            ax1.set_ylabel('Hit Rate (%)')
            ax1.set_title('Hit Rate vs Cache Size')
            ax1.grid(True, alpha=0.3)
            
            # Right: Effective latency vs cache
            ax2.plot(df['cache'], df['eff'], marker='s', linewidth=2, color='red')
            ax2.set_xlabel('Cache Size')
            ax2.set_ylabel('Effective Latency (µs)')
            ax2.set_title('Effective Latency vs Cache Size')
            ax2.grid(True, alpha=0.3)
        
        plt.tight_layout()
        output_path = self.output_dir / 'memory_pressure_curve.png'
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"  ✓ Saved: {output_path}")
        return output_path
    
    def generate_all(self):
        """Generate all graphs."""
        print("\n" + SEP)
        print("  HyperRAM Graph Generator")
        print(SEP)
        print(f"  Results directory: {self.results_dir}")
        print(f"  Output directory: {self.output_dir}")
        print(SEP)
        
        graphs = []
        
        graphs.append(self.plot_scalability())
        graphs.append(self.plot_hit_rate_sensitivity())
        graphs.append(self.plot_ai_model_comparison())
        graphs.append(self.plot_power_efficiency())
        graphs.append(self.plot_write_amplification())
        graphs.append(self.plot_memory_pressure())
        
        # Filter None values
        graphs = [g for g in graphs if g is not None]
        
        print("\n" + SEP)
        print(f"  Generated {len(graphs)} graphs:")
        for g in graphs:
            print(f"    ✓ {g.name}")
        print(SEP)
        
        return graphs


def main():
    parser = argparse.ArgumentParser(description='HyperRAM Graph Generator')
    parser.add_argument('--results', type=str, default='results', 
                       help='Results directory with CSV files')
    parser.add_argument('--output', type=str, default='figures',
                       help='Output directory for graphs')
    parser.add_argument('--scalability', action='store_true',
                       help='Generate scalability curve only')
    parser.add_argument('--ai-comparison', action='store_true',
                       help='Generate AI comparison only')
    parser.add_argument('--power', action='store_true',
                       help='Generate power efficiency plot only')
    parser.add_argument('--write-amp', action='store_true',
                       help='Generate write amplification plot only')
    
    args = parser.parse_args()
    
    plotter = HyperRAMPlotter(results_dir=args.results, output_dir=args.output)
    
    if args.scalability:
        plotter.plot_scalability()
    elif args.ai_comparison:
        plotter.plot_ai_model_comparison()
    elif args.power:
        plotter.plot_power_efficiency()
    elif args.write_amp:
        plotter.plot_write_amplification()
    else:
        plotter.generate_all()


if __name__ == "__main__":
    sys.exit(main())
```

---

## Usage Examples

### Generate All Graphs
```bash
cd hyperram-daemon
python plot_results.py
# Output: figures/ directory with 6 PNG files
```

### Generate Specific Graph
```bash
# Just scalability
python plot_results.py --scalability

# Just AI comparison
python plot_results.py --ai-comparison
```

### Custom Directories
```bash
python plot_results.py --results results/paper_20260611_120000 --output paper_figures
```

---

## Output Files

Generates publication-ready PNG files (300 DPI):

1. **scalability_curve.png** - Threads vs throughput with ideal scaling line
2. **hit_rate_sensitivity.png** - Dual panel: latency vs hit rate
3. **ai_model_comparison.png** - 2×2 grid: tokens/sec, hit rate, SSD reads, compression
4. **power_efficiency.png** - Scatter plot: ops/joule vs latency
5. **write_amplification.png** - Grouped bar chart by workload
6. **memory_pressure_curve.png** - Hit rate and latency vs cache size

---

## Paper Integration

Include these figures in your paper:

- **Figure 1:** Scalability curve (§4.4)
- **Figure 2:** Hit-rate sensitivity (§4.3)
- **Figure 3:** AI model comparison (§4.2)
- **Figure 4:** Power efficiency (§4.8)
- **Figure 5:** Write amplification (§4.9)
- **Figure 6:** Memory pressure curve (§4.10)

**After running benchmarks:**
```bash
python run_all_benchmarks.py --ollama
python plot_results.py
```

This generates all tables and figures needed for paper submission.