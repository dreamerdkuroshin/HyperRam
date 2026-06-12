# -*- coding: utf-8 -*-
r"""
============================================================================
  plot_results.py — HyperRAM Visual Graph Generator
============================================================================
  Creates publication-quality graphs for paper submission:
    - Scalability curve (throughput vs threads)
    - AI model performance comparison
    - Cache hit rate sensitivity
    - Tail latency distribution
    - Statistical percentile analysis
  
  Output: PNG files (300 DPI) ready for paper inclusion
  
  Usage:
    python plot_results.py --all
    python plot_results.py --scalability
    python plot_results.py --ai-benchmark
    python plot_results.py --tail-latency
============================================================================
"""
import sys, os, csv, statistics
from datetime import datetime
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    print("Warning: matplotlib not installed. Install with: pip install matplotlib")

PAGE_SIZE = 4096
SEP = "=" * 72

class GraphGenerator:
    """Generates publication-quality graphs from benchmark results."""
    
    def __init__(self, results_dir='results', output_dir='results/graphs'):
        self.results_dir = Path(results_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.scalability_data = []
        self.ai_data = []
        self.multithread_data = []
        
    def load_data(self):
        """Load all CSV results."""
        print("Loading benchmark data...")
        
        # Load scalability data
        mt_files = list(self.results_dir.glob('multithread_benchmark_*.csv'))
        if mt_files:
            with open(mt_files[-1], 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                self.multithread_data = list(reader)
            print(f"  Loaded multithread: {len(self.multithread_data)} points")
        
        # Load AI benchmark data
        ai_files = list(self.results_dir.glob('ollama_benchmark_*.csv'))
        for ai_file in ai_files:
            with open(ai_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                self.ai_data.extend(list(reader))
        print(f"  Loaded AI benchmarks: {len(self.ai_data)} results")
    
    def generate_scalability_graph(self):
        """Generate throughput vs threads scalability curve."""
        if not HAS_MATPLOTLIB or not self.multithread_data:
            print("  Skipping scalability graph (no data or matplotlib)")
            return None
        
        print("  Generating scalability graph...")
        
        # Extract data
        threads = []
        throughput = []
        hit_rates = []
        
        for row in sorted(self.multithread_data, key=lambda x: int(x.get('threads', 0))):
            threads.append(int(row.get('threads', 0)))
            throughput.append(float(row.get('throughput_ops_sec', 0)))
            hit_rates.append(float(row.get('hit_rate_pct', 0)))
        
        # Create figure with dual y-axis
        fig, ax1 = plt.subplots(figsize=(10, 6))
        
        # Throughput (left y-axis)
        color1 = 'tab:blue'
        ax1.set_xlabel('Threads', fontsize=12, fontweight='bold')
        ax1.set_ylabel('Throughput (ops/sec)', color=color1, fontsize=12, fontweight='bold')
        ax1.plot(threads, throughput, color=color1, marker='o', linewidth=2, markersize=8, label='Throughput')
        ax1.tick_params(axis='y', labelcolor=color1)
        ax1.grid(True, alpha=0.3)
        
        # Hit rate (right y-axis)
        ax2 = ax1.twinx()
        color2 = 'tab:orange'
        ax2.set_ylabel('Cache Hit Rate (%)', color=color2, fontsize=12, fontweight='bold')
        ax2.plot(threads, hit_rates, color=color2, marker='s', linewidth=2, markersize=8, label='Hit Rate')
        ax2.tick_params(axis='y', labelcolor=color2)
        
        # Title and layout
        plt.title('HyperRAM Scalability: Throughput vs Thread Count', fontsize=14, fontweight='bold')
        fig.tight_layout()
        
        # Save
        output_path = self.output_dir / 'scalability_curve.png'
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"    Saved: {output_path}")
        return output_path
    
    def generate_ai_performance_graph(self):
        """Generate AI model performance comparison bar chart."""
        if not HAS_MATPLOTLIB or not self.ai_data:
            print("  Skipping AI performance graph (no data or matplotlib)")
            return None
        
        print("  Generating AI performance graph...")
        
        # Aggregate by model
        model_performance = {}
        for row in self.ai_data:
            model = row.get('display_name', row.get('model', 'Unknown'))
            tps = float(row.get('tokens_per_sec', 0))
            hit_rate = float(row.get('hit_rate_pct', 0))
            
            if model not in model_performance:
                model_performance[model] = {'tps': [], 'hit_rate': []}
            model_performance[model]['tps'].append(tps)
            model_performance[model]['hit_rate'].append(hit_rate)
        
        # Calculate averages
        models = sorted(model_performance.keys(), key=lambda m: statistics.mean(model_performance[m]['tps']), reverse=True)
        avg_tps = [statistics.mean(model_performance[m]['tps']) for m in models]
        avg_hit_rate = [statistics.mean(model_performance[m]['hit_rate']) for m in models]
        
        # Create figure
        fig, ax1 = plt.subplots(figsize=(12, 6))
        
        # Tokens/sec (left y-axis)
        color1 = 'tab:blue'
        ax1.set_xlabel('Model', fontsize=12, fontweight='bold')
        ax1.set_ylabel('Tokens/sec', color=color1, fontsize=12, fontweight='bold')
        bars1 = ax1.bar(range(len(models)), avg_tps, color=color1, alpha=0.7, label='Tokens/sec')
        ax1.tick_params(axis='y', labelcolor=color1)
        ax1.set_xticks(range(len(models)))
        ax1.set_xticklabels(models, rotation=45, ha='right', fontsize=10)
        ax1.grid(True, alpha=0.3, axis='y')
        
        # Add value labels on bars
        for i, v in enumerate(avg_tps):
            ax1.text(i, v, f'{v:.1f}', ha='center', va='bottom', fontsize=9)
        
        # Hit rate (right y-axis)
        ax2 = ax1.twinx()
        color2 = 'tab:orange'
        ax2.set_ylabel('Cache Hit Rate (%)', color=color2, fontsize=12, fontweight='bold')
        bars2 = ax2.bar(range(len(models)), avg_hit_rate, color=color2, alpha=0.5, label='Hit Rate')
        ax2.tick_params(axis='y', labelcolor=color2)
        ax2.set_ylim(0, 100)
        
        # Add value labels
        for i, v in enumerate(avg_hit_rate):
            ax2.text(i, v, f'{v:.1f}%', ha='center', va='bottom', fontsize=9, color=color2)
        
        # Legend
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper right')
        
        plt.title('AI Model Performance Comparison (Ollama)', fontsize=14, fontweight='bold')
        fig.tight_layout()
        
        # Save
        output_path = self.output_dir / 'ai_performance_comparison.png'
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"    Saved: {output_path}")
        return output_path
    
    def generate_hit_rate_sensitivity_graph(self):
        """Generate effective latency vs hit rate sensitivity curve."""
        if not HAS_MATPLOTLIB:
            print("  Skipping hit rate graph (matplotlib not available)")
            return None
        
        print("  Generating hit rate sensitivity graph...")
        
        # Theoretical curve based on standard tiered memory model
        hit_rates = [50, 60, 70, 80, 85, 90, 95, 97, 99, 99.5, 99.9]
        ram_latency = 0.1  # µs (hypothetical)
        ssd_latency = 25.0  # µs (hypothetical)
        
        effective_latencies = []
        for hr in hit_rates:
            hr_decimal = hr / 100
            eff_lat = (hr_decimal * ram_latency) + ((1 - hr_decimal) * ssd_latency)
            effective_latencies.append(eff_lat)
        
        # Create figure
        fig, ax = plt.subplots(figsize=(10, 6))
        
        ax.plot(hit_rates, effective_latencies, color='tab:red', marker='o', linewidth=2, markersize=8)
        ax.set_xlabel('Cache Hit Rate (%)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Effective Latency (µs)', fontsize=12, fontweight='bold')
        ax.set_title('Hit-Rate Sensitivity: Effective Latency vs Cache Hit Rate', fontsize=14, fontweight='bold')
        ax.grid(True, alpha=0.3)
        ax.set_xlim(50, 100)
        
        # Add annotations
        for i, hr in enumerate(hit_rates):
            if hr in [80, 90, 95, 99]:
                ax.annotate(f'{effective_latencies[i]:.2f} µs', 
                           xy=(hr, effective_latencies[i]), 
                           xytext=(5, 5), 
                           textcoords='offset points',
                           fontsize=9)
        
        fig.tight_layout()
        
        # Save
        output_path = self.output_dir / 'hit_rate_sensitivity.png'
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"    Saved: {output_path}")
        return output_path
    
    def generate_tail_latency_graph(self):
        """Generate tail latency percentile distribution."""
        if not HAS_MATPLOTLIB or not self.multithread_data:
            print("  Skipping tail latency graph (no data or matplotlib)")
            return None
        
        print("  Generating tail latency graph...")
        
        # Extract percentile data
        threads = []
        p50 = []
        p95 = []
        p99 = []
        
        for row in sorted(self.multithread_data, key=lambda x: int(x.get('threads', 0))):
            threads.append(int(row.get('threads', 0)))
            p50.append(float(row.get('median_latency_us', 0)))
            p95.append(float(row.get('p95_latency_us', 0)))
            p99.append(float(row.get('p99_latency_us', 0)))
        
        # Create figure
        fig, ax = plt.subplots(figsize=(10, 6))
        
        x = range(len(threads))
        width = 0.25
        
        bars1 = ax.bar([i - width for i in x], p50, width, label='P50 (Median)', color='tab:blue')
        bars2 = ax.bar(x, p95, width, label='P95', color='tab:orange')
        bars3 = ax.bar([i + width for i in x], p99, width, label='P99', color='tab:red')
        
        ax.set_xlabel('Threads', fontsize=12, fontweight='bold')
        ax.set_ylabel('Latency (µs)', fontsize=12, fontweight='bold')
        ax.set_title('Tail Latency Distribution Across Thread Counts', fontsize=14, fontweight='bold')
        ax.set_xticks(x)
        ax.set_xticklabels(threads)
        ax.legend()
        ax.grid(True, alpha=0.3, axis='y')
        
        fig.tight_layout()
        
        # Save
        output_path = self.output_dir / 'tail_latency_distribution.png'
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"    Saved: {output_path}")
        return output_path
    
    def generate_speedup_efficiency_graph(self):
        """Generate speedup and parallel efficiency graph."""
        if not HAS_MATPLOTLIB or not self.multithread_data:
            print("  Skipping speedup graph (no data or matplotlib)")
            return None
        
        print("  Generating speedup/efficiency graph...")
        
        # Calculate speedup and efficiency
        threads = []
        speedup = []
        efficiency = []
        
        baseline = None
        for row in sorted(self.multithread_data, key=lambda x: int(x.get('threads', 0))):
            t = int(row.get('threads', 0))
            tput = float(row.get('throughput_ops_sec', 0))
            
            if baseline is None:
                baseline = tput
                speedup.append(1.0)
            else:
                speedup.append(tput / baseline)
            
            threads.append(t)
            eff = (speedup[-1] / t * 100) if t > 0 else 0
            efficiency.append(eff)
        
        # Create figure
        fig, ax1 = plt.subplots(figsize=(10, 6))
        
        # Speedup (left y-axis)
        color1 = 'tab:green'
        ax1.set_xlabel('Threads', fontsize=12, fontweight='bold')
        ax1.set_ylabel('Speedup (×)', color=color1, fontsize=12, fontweight='bold')
        ax1.plot(threads, speedup, color=color1, marker='o', linewidth=2, markersize=8, label='Speedup')
        ax1.axhline(y=1.0, color='gray', linestyle='--', linewidth=1, alpha=0.5)
        ax1.tick_params(axis='y', labelcolor=color1)
        ax1.grid(True, alpha=0.3)
        
        # Efficiency (right y-axis)
        ax2 = ax1.twinx()
        color2 = 'tab:purple'
        ax2.set_ylabel('Parallel Efficiency (%)', color=color2, fontsize=12, fontweight='bold')
        ax2.plot(threads, efficiency, color=color2, marker='s', linewidth=2, markersize=8, label='Efficiency')
        ax2.axhline(y=100, color='gray', linestyle='--', linewidth=1, alpha=0.5)
        ax2.axhline(y=80, color='gray', linestyle=':', linewidth=1, alpha=0.5)
        ax2.tick_params(axis='y', labelcolor=color2)
        ax2.set_ylim(0, 120)
        
        plt.title('Parallel Speedup and Efficiency vs Thread Count', fontsize=14, fontweight='bold')
        fig.tight_layout()
        
        # Save
        output_path = self.output_dir / 'speedup_efficiency.png'
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"    Saved: {output_path}")
        return output_path
    
    def generate_all_graphs(self):
        """Generate all graphs."""
        print("\n" + SEP)
        print("  Generating Visual Graphs")
        print(SEP)
        
        self.load_data()
        
        graphs = []
        graphs.append(self.generate_scalability_graph())
        graphs.append(self.generate_ai_performance_graph())
        graphs.append(self.generate_hit_rate_sensitivity_graph())
        graphs.append(self.generate_tail_latency_graph())
        graphs.append(self.generate_speedup_efficiency_graph())
        
        # Filter None values
        graphs = [g for g in graphs if g is not None]
        
        print(f"\n  Generated {len(graphs)} graphs in: {self.output_dir}")
        print("\n" + SEP)
        print("  ✓ Graph Generation Complete")
        print(SEP)
        
        return graphs


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Generate HyperRAM Visual Graphs')
    parser.add_argument('--all', action='store_true', help='Generate all graphs')
    parser.add_argument('--scalability', action='store_true', help='Generate scalability curve')
    parser.add_argument('--ai', action='store_true', help='Generate AI performance comparison')
    parser.add_argument('--hit-rate', action='store_true', help='Generate hit rate sensitivity')
    parser.add_argument('--tail-latency', action='store_true', help='Generate tail latency')
    parser.add_argument('--speedup', action='store_true', help='Generate speedup/efficiency')
    parser.add_argument('--output-dir', type=str, default='results/graphs', help='Output directory')
    parser.add_argument('--results-dir', type=str, default='results', help='Results directory')
    
    args = parser.parse_args()
    
    if not any([args.all, args.scalability, args.ai, args.hit_rate, args.tail_latency, args.speedup]):
        args.all = True
    
    generator = GraphGenerator(
        results_dir=args.results_dir,
        output_dir=args.output_dir
    )
    
    if args.all:
        generator.generate_all_graphs()
    else:
        generator.load_data()
        if args.scalability:
            generator.generate_scalability_graph()
        if args.ai:
            generator.generate_ai_performance_graph()
        if args.hit_rate:
            generator.generate_hit_rate_sensitivity_graph()
        if args.tail_latency:
            generator.generate_tail_latency_graph()
        if args.speedup:
            generator.generate_speedup_efficiency_graph()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())