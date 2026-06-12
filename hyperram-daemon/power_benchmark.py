# -*- coding: utf-8 -*-
r"""
=============================================================================
  power_benchmark.py  —  HyperRAM Power Consumption Analysis
=============================================================================
  Measures power consumption for:
    - RAM-only access (kernel cache hits)
    - SSD tiered memory (kernel cache misses)
    - Userspace fallback

  Metrics:
    - Watts (average power)
    - Joules per inference (energy efficiency)
    - Performance-per-watt

  Usage:
    python power_benchmark.py
    python power_benchmark.py --pages 2000 --reads 10000
    python power_benchmark.py --kernel-only
    python power_benchmark.py --userspace-only
=============================================================================
"""
import sys, os
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import time, random, statistics, csv, argparse, struct
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from kernel_client import HyperRAMKernelClient
from core import HyperRAMEngine

PAGE_SIZE = 4096
SEP = "=" * 72

# ---------------------------------------------------------------------------
# Power estimation model (based on typical hardware)
# ---------------------------------------------------------------------------
# Reference: DDR4 RAM ~3-5W per 8GB, NVMe SSD ~5-8W active
# These are estimates - real measurements require hardware sensors
POWER_MODEL = {
    'ram_active_w': 4.0,      # DDR4 active power (8GB stick)
    'ram_idle_w': 1.5,        # DDR4 idle power
    'nvme_active_w': 6.5,     # NVMe SSD active (read/write)
    'nvme_idle_w': 0.8,       # NVMe SSD idle
    'cpu_per_io_w': 0.5,      # CPU overhead per I/O operation
}

class PowerMeter:
    """
    Simulated power meter using hardware performance counters.
    In production, this would read from:
      - Intel RAPL (Running Average Power Limit)
      - Windows Power Troubleshooter API
      - Hardware sensors (INA219, etc.)
    """
    
    def __init__(self):
        self.samples = []
        self.start_time = None
        self.total_joules = 0.0
        
    def start(self):
        self.start_time = time.perf_counter()
        self.samples = []
        
    def sample(self, ram_active=False, nvme_active=False, io_ops=0):
        """Record a power sample based on current activity."""
        power_w = 0.0
        
        # Base system power (idle)
        power_w += POWER_MODEL['ram_idle_w']
        power_w += POWER_MODEL['nvme_idle_w']
        
        # Active components
        if ram_active:
            power_w += POWER_MODEL['ram_active_w'] - POWER_MODEL['ram_idle_w']
        if nvme_active:
            power_w += POWER_MODEL['nvme_active_w'] - POWER_MODEL['nvme_idle_w']
            
        # CPU I/O overhead
        power_w += io_ops * POWER_MODEL['cpu_per_io_w']
        
        self.samples.append({
            'timestamp': time.perf_counter() - self.start_time,
            'power_w': power_w,
            'ram_active': ram_active,
            'nvme_active': nvme_active
        })
        
    def get_total_joules(self):
        """Calculate total energy consumed (Joules = Watts × seconds)."""
        if len(self.samples) < 2:
            return 0.0
            
        total_joules = 0.0
        for i in range(1, len(self.samples)):
            dt = self.samples[i]['timestamp'] - self.samples[i-1]['timestamp']
            avg_power = (self.samples[i]['power_w'] + self.samples[i-1]['power_w']) / 2
            total_joules += avg_power * dt
            
        self.total_joules = total_joules
        return total_joules
        
    def get_avg_power(self):
        """Average power consumption in Watts."""
        if not self.samples:
            return 0.0
        return statistics.mean([s['power_w'] for s in self.samples])
        
    def get_report(self):
        """Generate power report."""
        if not self.samples:
            return {}
            
        ram_samples = [s for s in self.samples if s['ram_active']]
        nvme_samples = [s for s in self.samples if s['nvme_active']]
        
        return {
            'avg_power_w': self.get_avg_power(),
            'total_joules': self.get_total_joules(),
            'ram_active_pct': (len(ram_samples) / len(self.samples) * 100) if self.samples else 0,
            'nvme_active_pct': (len(nvme_samples) / len(self.samples) * 100) if self.samples else 0,
            'peak_power_w': max([s['power_w'] for s in self.samples]),
            'samples': len(self.samples)
        }


def run_power_benchmark(read_fn, write_fn, n_pages, n_reads, label, meter):
    """
    Run benchmark with power monitoring.
    
    Returns dict with power metrics.
    """
    print(f"\n  [{label}] Filling {n_pages} pages...", flush=True)
    meter.start()
    
    t_fill = time.perf_counter()
    for i in range(n_pages):
        write_fn(i, bytes([i & 0xFF]) * PAGE_SIZE)
        meter.sample(ram_active=True, nvme_active=False, io_ops=1)
    fill_s = time.perf_counter() - t_fill
    fill_mb_s = (n_pages * PAGE_SIZE / (1024**2)) / fill_s if fill_s > 0 else 0
    print(f"  [{label}] Fill done: {fill_s:.2f}s  ({fill_mb_s:.1f} MB/s)")
    
    # Warm-up
    hot = max(1, n_pages // 5)
    print(f"  [{label}] Warming up {hot} hot pages...", flush=True)
    for i in range(hot):
        read_fn(i)
        meter.sample(ram_active=True, nvme_active=False, io_ops=1)
    
    # Benchmark phase
    print(f"  [{label}] Running {n_reads} reads (80/20 Zipf)...", flush=True)
    hot = max(1, n_pages // 5)
    rng = random.Random(42)
    
    ram_hits = 0
    nvme_misses = 0
    latencies = []
    
    for _ in range(n_reads):
        pid = rng.randint(0, hot - 1) if rng.random() < 0.80 else rng.randint(0, n_pages - 1)
        
        t0 = time.perf_counter()
        read_fn(pid)
        lat = (time.perf_counter() - t0) * 1_000_000
        latencies.append(lat)
        
        # Classify access type
        if lat < 500:  # RAM hit threshold
            ram_hits += 1
            meter.sample(ram_active=True, nvme_active=False, io_ops=1)
        else:  # NVMe miss
            nvme_misses += 1
            meter.sample(ram_active=True, nvme_active=True, io_ops=1)
    
    # Generate report
    power_report = meter.get_report()
    total_ops = ram_hits + nvme_misses
    hit_rate = (ram_hits / total_ops * 100) if total_ops > 0 else 0
    
    joules_per_op = power_report['total_joules'] / total_ops if total_ops > 0 else 0
    ops_per_joule = total_ops / power_report['total_joules'] if power_report['total_joules'] > 0 else 0
    
    return {
        'label': label,
        'n_pages': n_pages,
        'n_reads': n_reads,
        'hit_rate_pct': hit_rate,
        'avg_latency_us': statistics.mean(latencies) if latencies else 0,
        'p50_latency_us': sorted(latencies)[len(latencies)//2] if latencies else 0,
        'p99_latency_us': sorted(latencies)[int(len(latencies)*0.99)] if latencies else 0,
        'avg_power_w': power_report['avg_power_w'],
        'total_joules': power_report['total_joules'],
        'joules_per_op': joules_per_op,
        'ops_per_joule': ops_per_joule,
        'ram_active_pct': power_report['ram_active_pct'],
        'nvme_active_pct': power_report['nvme_active_pct'],
        'peak_power_w': power_report['peak_power_w']
    }


def print_results(results):
    """Print formatted results table."""
    print("\n" + SEP)
    print("  Power Consumption Benchmark Results")
    print(SEP)
    
    header = (
        f"  {'Path':<20} | {'Hit Rate':>8} | {'Avg Lat':>10} | {'P99 Lat':>10} | "
        f"{'Avg Power':>10} | {'Total J':>10} | {'J/op':>8} | {'Op/J':>8}"
    )
    print(header)
    print("  " + "-" * 105)
    
    for r in results:
        print(f"  {r['label']:<20} | {r['hit_rate_pct']:>7.2f}% | "
              f"{r['avg_latency_us']:>9.2f} µs | {r['p99_latency_us']:>9.2f} µs | "
              f"{r['avg_power_w']:>9.3f} W | {r['total_joules']:>9.4f} J | "
              f"{r['joules_per_op']:>7.4f} | {r['ops_per_joule']:>7.1f}")
    
    print(SEP)
    
    # Find best efficiency
    best_eff = max(results, key=lambda x: x['ops_per_joule'])
    print(f"\n  Best Energy Efficiency: {best_eff['label']}")
    print(f"    {best_eff['ops_per_joule']:.1f} operations per Joule")
    print(f"    {best_eff['joules_per_op']:.4f} Joules per operation")
    
    # Calculate savings
    if len(results) >= 2:
        ram_result = next((r for r in results if 'RAM' in r['label']), None)
        tiered_result = next((r for r in results if 'Tiered' in r['label'] or 'SSD' in r['label']), None)
        
        if ram_result and tiered_result:
            energy_savings = (tiered_result['joules_per_op'] - ram_result['joules_per_op']) / tiered_result['joules_per_op'] * 100
            print(f"\n  Energy Savings (RAM vs Tiered): {energy_savings:.1f}%")


def save_csv(results, output_dir='results'):
    """Save results to CSV file."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"power_benchmark_{timestamp}.csv"
    filepath = os.path.join(output_dir, filename)
    
    if not results:
        return
        
    fieldnames = list(results[0].keys())
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\n  Results saved to: {filepath}")


def main():
    parser = argparse.ArgumentParser(description='HyperRAM Power Consumption Benchmark')
    parser.add_argument('--pages', type=int, default=2000, help='Number of pages to test')
    parser.add_argument('--reads', type=int, default=10000, help='Number of read operations')
    parser.add_argument('--kernel-only', action='store_true', help='Test kernel mode only')
    parser.add_argument('--userspace-only', action='store_true', help='Test userspace only')
    parser.add_argument('--output', type=str, default='results', help='Output directory')
    args = parser.parse_args()
    
    print("\n" + SEP)
    print("  HyperRAM Power Consumption Benchmark")
    print(SEP)
    print(f"  Pages: {args.pages} ({args.pages * PAGE_SIZE / 1024 / 1024:.1f} MB)")
    print(f"  Reads: {args.reads}")
    print(SEP)
    
    results = []
    
    # Kernel mode benchmark
    if not args.userspace_only:
        print("\n[1/2] Kernel Mode Benchmark")
        print("-" * 40)
        
        kc = HyperRAMKernelClient()
        if kc.is_kernel_mode:
            meter = PowerMeter()
            
            def kernel_read(pid):
                return kc.read_page(pid)
            def kernel_write(pid, data):
                kc.write_page(pid, data)
            
            result = run_power_benchmark(
                kernel_read, kernel_write,
                args.pages, args.reads,
                "Kernel (Real NVMe)", meter
            )
            results.append(result)
            print(f"  Hit Rate: {result['hit_rate_pct']:.2f}%")
            print(f"  Avg Power: {result['avg_power_w']:.3f} W")
            print(f"  Total Energy: {result['total_joules']:.4f} J")
        else:
            print("  [SKIP] Kernel driver not loaded")
        
        kc.close()
    
    # Userspace benchmark
    if not args.kernel_only:
        print("\n[2/2] Userspace Mode Benchmark")
        print("-" * 40)
        
        try:
            pool_path = os.path.join(os.path.dirname(__file__), "..", "hyperram.pool")
            engine = HyperRAMEngine(ssd_pool_path=os.path.abspath(pool_path))
            engine.max_ram_cache_pages = 256  # ~1GB RAM cache
            
            meter = PowerMeter()
            
            def user_read(pid):
                return engine.read_page(pid)
            def user_write(pid, data):
                engine.write_page(pid, data)
            
            result = run_power_benchmark(
                user_read, user_write,
                args.pages, args.reads,
                "Userspace (mmap)", meter
            )
            results.append(result)
            print(f"  Hit Rate: {result['hit_rate_pct']:.2f}%")
            print(f"  Avg Power: {result['avg_power_w']:.3f} W")
            print(f"  Total Energy: {result['total_joules']:.4f} J")
            
            engine.close()
        except Exception as e:
            print(f"  [ERROR] Userspace benchmark failed: {e}")
    
    # Print results
    if results:
        print_results(results)
        save_csv(results, args.output)
    else:
        print("\n  [ERROR] No benchmarks completed")
        sys.exit(1)
    
    print("\n" + SEP)
    print("  Benchmark Complete")
    print(SEP)


if __name__ == "__main__":
    main()