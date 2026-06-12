# -*- coding: utf-8 -*-
r"""
=============================================================================
  stability_test.py  —  HyperRAM Long-Duration Stability Test
=============================================================================
  Runs extended stress tests to detect:
    - Memory leaks
    - Data corruption over time
    - Performance degradation
    - Crash/recovery issues

  Duration options:
    - Quick: 1 hour
    - Standard: 24 hours
    - Extended: 72 hours

  Metrics tracked:
    - Operations completed
    - Error rate
    - Memory usage trend
    - Latency drift
    - Cache hit rate stability

  Usage:
    python stability_test.py --duration 24h
    python stability_test.py --duration 1h --quick
    python stability_test.py --duration 72h --output stability_results
=============================================================================
"""
import sys, os
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import time, random, statistics, csv, argparse, threading, psutil
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque

sys.path.insert(0, os.path.dirname(__file__))
from kernel_client import HyperRAMKernelClient
from core import HyperRAMEngine

PAGE_SIZE = 4096
SEP = "=" * 72

class StabilityMonitor:
    """Real-time stability monitoring and leak detection."""
    
    def __init__(self, window_size=100):
        self.window_size = window_size
        self.latency_window = deque(maxlen=window_size)
        self.error_window = deque(maxlen=window_size)
        self.hit_rate_window = deque(maxlen=window_size)
        self.memory_window = deque(maxlen=window_size)
        
        self.start_time = None
        self.total_ops = 0
        self.total_errors = 0
        self.start_memory_mb = None
        
        self.lock = threading.Lock()
        
    def start(self):
        self.start_time = time.time()
        self.start_memory_mb = self._get_memory_usage()
        
    def record_op(self, latency_us, is_hit, error=False):
        with self.lock:
            self.total_ops += 1
            if error:
                self.total_errors += 1
                self.error_window.append(1)
            else:
                self.error_window.append(0)
                self.latency_window.append(latency_us)
                
            self.hit_rate_window.append(1 if is_hit else 0)
            self.memory_window.append(self._get_memory_usage())
            
    def _get_memory_usage(self):
        """Get current process memory in MB."""
        try:
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / (1024 * 1024)
        except:
            return 0.0
            
    def get_report(self):
        """Generate stability report."""
        with self.lock:
            elapsed = time.time() - self.start_time if self.start_time else 0
            
            # Calculate metrics
            avg_latency = statistics.mean(self.latency_window) if self.latency_window else 0
            error_rate = sum(self.error_window) / len(self.error_window) if self.error_window else 0
            hit_rate = sum(self.hit_rate_window) / len(self.hit_rate_window) if self.hit_rate_window else 0
            
            # Memory trend
            if len(self.memory_window) >= 2:
                memory_start = self.memory_window[0]
                memory_end = self.memory_window[-1]
                memory_leak_mb = memory_end - memory_start
                memory_leak_rate = memory_leak_mb / (elapsed / 3600) if elapsed > 0 else 0  # MB/hour
            else:
                memory_leak_mb = 0
                memory_leak_rate = 0
                
            # Latency drift
            if len(self.latency_window) >= self.window_size:
                early_lats = list(self.latency_window)[:self.window_size//2]
                late_lats = list(self.latency_window)[self.window_size//2:]
                latency_drift = (statistics.mean(late_lats) - statistics.mean(early_lats)) / statistics.mean(early_lats) * 100 if statistics.mean(early_lats) > 0 else 0
            else:
                latency_drift = 0
                
            return {
                'elapsed_hours': elapsed / 3600,
                'total_ops': self.total_ops,
                'total_errors': self.total_errors,
                'error_rate_pct': error_rate * 100,
                'ops_per_hour': self.total_ops / (elapsed / 3600) if elapsed > 0 else 0,
                'avg_latency_us': avg_latency,
                'latency_drift_pct': latency_drift,
                'hit_rate_pct': hit_rate * 100,
                'memory_start_mb': self.start_memory_mb,
                'memory_current_mb': self.memory_window[-1] if self.memory_window else 0,
                'memory_leak_mb': memory_leak_mb,
                'memory_leak_rate_mb_per_hour': memory_leak_rate,
                'is_stable': error_rate < 0.01 and abs(latency_drift) < 20 and memory_leak_rate < 10
            }


def run_stability_test(client_factory, duration_hours, n_pages, ops_per_sec, monitor, stop_event):
    """
    Run continuous operations for specified duration.
    
    Args:
        client_factory: Function to create client
        duration_hours: Test duration in hours
        n_pages: Working set size
        ops_per_sec: Target operations per second
        monitor: StabilityMonitor instance
        stop_event: Threading event to signal stop
    """
    client = client_factory()
    rng = random.Random(42)
    
    hot_pages = max(1, n_pages // 5)
    ops_interval = 1.0 / ops_per_sec if ops_per_sec > 0 else 0
    
    start_time = time.time()
    end_time = start_time + (duration_hours * 3600)
    
    print(f"  Starting stability test for {duration_hours} hours...")
    print(f"  Target: {ops_per_sec} ops/sec, {n_pages} pages")
    
    op_count = 0
    while time.time() < end_time and not stop_event.is_set():
        op_start = time.time()
        
        # Perform operation
        try:
            # 80% reads, 20% writes
            if rng.random() < 0.80:
                page_id = rng.randint(0, hot_pages - 1) if rng.random() < 0.80 else rng.randint(0, n_pages - 1)
                t0 = time.perf_counter()
                data = client.read_page(page_id)
                lat_us = (time.perf_counter() - t0) * 1_000_000
                is_hit = lat_us < 500
                monitor.record_op(lat_us, is_hit, error=False)
            else:
                page_id = rng.randint(0, n_pages - 1)
                t0 = time.perf_counter()
                data = bytes([rng.randint(0, 255)]) * PAGE_SIZE
                client.write_page(page_id, data)
                lat_us = (time.perf_counter() - t0) * 1_000_000
                monitor.record_op(lat_us, True, error=False)
                
            op_count += 1
        except Exception as e:
            monitor.record_op(0, False, error=True)
            print(f"  [ERROR] Operation failed: {e}")
        
        # Rate limiting
        elapsed = time.time() - op_start
        if elapsed < ops_interval:
            time.sleep(ops_interval - elapsed)
        
        # Progress report every 5 minutes
        if op_count % (ops_per_sec * 300) < ops_per_sec:
            report = monitor.get_report()
            hours = report['elapsed_hours']
            print(f"    [{hours:.2f}h] Ops: {report['total_ops']}, "
                  f"Errors: {report['total_errors']}, "
                  f"Hit Rate: {report['hit_rate_pct']:.1f}%, "
                  f"Memory: {report['memory_current_mb']:.0f} MB")
    
    client.close()
    print(f"  Test completed. Total ops: {op_count}")


def print_final_report(monitor):
    """Print final stability report."""
    report = monitor.get_report()
    
    print("\n" + SEP)
    print("  Final Stability Report")
    print(SEP)
    
    print(f"  Duration:          {report['elapsed_hours']:.2f} hours")
    print(f"  Total Operations:  {report['total_ops']:,}")
    print(f"  Total Errors:      {report['total_errors']:,}")
    print(f"  Error Rate:        {report['error_rate_pct']:.4f}%")
    print(f"  Ops/Hour:          {report['ops_per_hour']:,.0f}")
    print()
    print(f"  Avg Latency:       {report['avg_latency_us']:.2f} µs")
    print(f"  Latency Drift:     {report['latency_drift_pct']:.1f}%")
    print(f"  Hit Rate:          {report['hit_rate_pct']:.2f}%")
    print()
    print(f"  Memory Start:      {report['memory_start_mb']:.1f} MB")
    print(f"  Memory Current:    {report['memory_current_mb']:.1f} MB")
    print(f"  Memory Leak:       {report['memory_leak_mb']:.2f} MB")
    print(f"  Leak Rate:         {report['memory_leak_rate_mb_per_hour']:.2f} MB/hour")
    print()
    
    # Stability verdict
    print("  Stability Analysis:")
    if report['is_stable']:
        print("    ✓ PASS - System is stable")
        print(f"      - Error rate < 1%: {'✓' if report['error_rate_pct'] < 1 else '✗'}")
        print(f"      - Latency drift < 20%: {'✓' if abs(report['latency_drift_pct']) < 20 else '✗'}")
        print(f"      - Memory leak < 10 MB/hour: {'✓' if report['memory_leak_rate_mb_per_hour'] < 10 else '✗'}")
    else:
        print("    ✗ FAIL - Stability issues detected")
        if report['error_rate_pct'] >= 1:
            print(f"      - High error rate: {report['error_rate_pct']:.2f}%")
        if abs(report['latency_drift_pct']) >= 20:
            print(f"      - Significant latency drift: {report['latency_drift_pct']:.1f}%")
        if report['memory_leak_rate_mb_per_hour'] >= 10:
            print(f"      - Memory leak detected: {report['memory_leak_rate_mb_per_hour']:.2f} MB/hour")
    
    print(SEP)


def save_csv(monitor, output_dir='results'):
    """Save stability metrics to CSV."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"stability_test_{timestamp}.csv"
    filepath = os.path.join(output_dir, filename)
    
    report = monitor.get_report()
    
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        f.write("metric,value\n")
        for key, value in report.items():
            f.write(f"{key},{value}\n")
    
    print(f"\n  Results saved to: {filepath}")


def parse_duration(duration_str):
    """Parse duration string (e.g., '1h', '24h', '72h')."""
    duration_str = duration_str.lower().strip()
    if duration_str.endswith('h'):
        return float(duration_str[:-1])
    elif duration_str.endswith('m'):
        return float(duration_str[:-1]) / 60
    else:
        return float(duration_str)  # Assume hours


def main():
    parser = argparse.ArgumentParser(description='HyperRAM Long-Duration Stability Test')
    parser.add_argument('--duration', type=str, default='1h', help='Test duration (e.g., 1h, 24h, 72h)')
    parser.add_argument('--pages', type=int, default=1000, help='Working set size in pages')
    parser.add_argument('--ops-per-sec', type=int, default=100, help='Target operations per second')
    parser.add_argument('--kernel-only', action='store_true', help='Test kernel mode only')
    parser.add_argument('--userspace-only', action='store_true', help='Test userspace only')
    parser.add_argument('--output', type=str, default='results', help='Output directory')
    parser.add_argument('--quick', action='store_true', help='Quick test (1 hour)')
    args = parser.parse_args()
    
    duration_hours = parse_duration(args.duration)
    if args.quick:
        duration_hours = min(duration_hours, 1.0)
    
    print("\n" + SEP)
    print("  HyperRAM Long-Duration Stability Test")
    print(SEP)
    print(f"  Duration: {duration_hours:.1f} hours")
    print(f"  Working Set: {args.pages} pages ({args.pages * PAGE_SIZE / 1024 / 1024:.1f} MB)")
    print(f"  Target Rate: {args.ops_per_sec} ops/sec")
    print(SEP)
    
    stop_event = threading.Event()
    
    # Kernel mode test
    if not args.userspace_only:
        print("\n[1/2] Kernel Mode Stability Test")
        print("-" * 50)
        
        kc = HyperRAMKernelClient()
        if kc.is_kernel_mode:
            # Pre-fill pages
            print(f"  Pre-filling {args.pages} pages...")
            for i in range(args.pages):
                kc.write_page(i, bytes([i & 0xFF]) * PAGE_SIZE)
            kc.close()
            
            monitor = StabilityMonitor(window_size=1000)
            monitor.start()
            
            def kernel_factory():
                return HyperRAMKernelClient()
            
            try:
                run_stability_test(
                    kernel_factory,
                    duration_hours,
                    args.pages,
                    args.ops_per_sec,
                    monitor,
                    stop_event
                )
                print_final_report(monitor)
                save_csv(monitor, args.output)
            except KeyboardInterrupt:
                print("\n  [INTERRUPTED] Stopping test early...")
                stop_event.set()
                time.sleep(2)
                print_final_report(monitor)
        else:
            print("  [SKIP] Kernel driver not loaded")
        
        kc.close()
    
    # Userspace test
    if not args.kernel_only:
        print("\n[2/2] Userspace Mode Stability Test")
        print("-" * 50)
        
        try:
            pool_path = os.path.join(os.path.dirname(__file__), "..", "hyperram.pool")
            base_engine = HyperRAMEngine(ssd_pool_path=os.path.abspath(pool_path))
            base_engine.max_ram_cache_pages = 256
            
            # Pre-fill pages
            print(f"  Pre-filling {args.pages} pages...")
            for i in range(args.pages):
                base_engine.write_page(i, bytes([i & 0xFF]) * PAGE_SIZE)
            base_engine.close()
            
            monitor = StabilityMonitor(window_size=1000)
            monitor.start()
            
            def user_factory():
                engine = HyperRAMEngine(ssd_pool_path=os.path.abspath(pool_path))
                engine.max_ram_cache_pages = 256
                return engine
            
            try:
                run_stability_test(
                    user_factory,
                    duration_hours,
                    args.pages,
                    args.ops_per_sec,
                    monitor,
                    stop_event
                )
                print_final_report(monitor)
                save_csv(monitor, args.output)
            except KeyboardInterrupt:
                print("\n  [INTERRUPTED] Stopping test early...")
                stop_event.set()
                time.sleep(2)
                print_final_report(monitor)
                
        except Exception as e:
            print(f"  [ERROR] Userspace test failed: {e}")
    
    print("\n" + SEP)
    print("  Stability Test Complete")
    print(SEP)


if __name__ == "__main__":
    main()