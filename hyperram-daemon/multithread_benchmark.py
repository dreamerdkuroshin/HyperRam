# -*- coding: utf-8 -*-
r"""
=============================================================================
  multithread_benchmark.py  —  HyperRAM Multi-threaded Performance Test
=============================================================================
  Measures performance under concurrent access:
    - 1 thread (baseline)
    - 4 threads (moderate concurrency)
    - 8 threads (high concurrency)
    - 16 threads (extreme concurrency)

  Metrics:
    - Aggregate throughput (ops/sec)
    - Per-thread latency
    - Cache hit rate under contention
    - Lock contention analysis

  Usage:
    python multithread_benchmark.py
    python multithread_benchmark.py --threads 4,8,16
    python multithread_benchmark.py --pages 5000 --reads-per-thread 2000
=============================================================================
"""
import sys, os
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import time, random, statistics, csv, argparse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from kernel_client import HyperRAMKernelClient
from core import HyperRAMEngine, QoSTag

PAGE_SIZE = 4096
SEP = "=" * 72

class ThreadStats:
    """Per-thread statistics collector."""
    def __init__(self):
        self.lock = threading.Lock()
        self.latencies = []
        self.hits = 0
        self.misses = 0
        self.errors = 0
        
    def record(self, latency_us, is_hit):
        with self.lock:
            self.latencies.append(latency_us)
            if is_hit:
                self.hits += 1
            else:
                self.misses += 1
                
    def record_error(self):
        with self.lock:
            self.errors += 1
            
    def get_report(self):
        with self.lock:
            if not self.latencies:
                return {}
            sorted_lats = sorted(self.latencies)
            return {
                'total_ops': len(self.latencies),
                'hits': self.hits,
                'misses': self.misses,
                'errors': self.errors,
                'hit_rate_pct': (self.hits / (self.hits + self.misses) * 100) if (self.hits + self.misses) > 0 else 0,
                'avg_latency_us': statistics.mean(self.latencies),
                'median_latency_us': sorted_lats[len(sorted_lats)//2],
                'p90_latency_us': sorted_lats[int(len(sorted_lats)*0.90)],
                'p99_latency_us': sorted_lats[int(len(sorted_lats)*0.99)],
                'p999_latency_us': sorted_lats[min(int(len(sorted_lats)*0.999), len(sorted_lats)-1)],
                'min_latency_us': min(self.latencies),
                'max_latency_us': max(self.latencies)
            }


def worker_thread(client_factory, thread_id, n_pages, n_reads, stats):
    """
    Worker thread function.
    
    Args:
        client_factory: Function to create a new client instance
        thread_id: Thread identifier
        n_pages: Working set size
        n_reads: Number of reads per thread
        stats: ThreadStats object to record results
    """
    try:
        client = client_factory()
        rng = random.Random(thread_id * 1000)  # Unique seed per thread
        
        # Hot set (80% of accesses)
        hot_pages = max(1, n_pages // 5)
        
        for i in range(n_reads):
            # Zipf-like distribution: 80% hot, 20% cold
            if rng.random() < 0.80:
                page_id = rng.randint(0, hot_pages - 1)
            else:
                page_id = rng.randint(0, n_pages - 1)
            
            # Measure latency
            t0 = time.perf_counter()
            try:
                data = client.read_page(page_id)
                lat_us = (time.perf_counter() - t0) * 1_000_000
                
                # Classify as hit/miss based on latency
                is_hit = lat_us < 500  # 500µs threshold
                stats.record(lat_us, is_hit)
            except Exception as e:
                stats.record_error()
        
        client.close()
    except Exception as e:
        stats.record_error()
        print(f"  [Thread {thread_id}] Error: {e}")


def run_multithread_benchmark(client_factory, n_pages, n_reads_per_thread, thread_counts, label):
    """
    Run benchmark with varying thread counts.
    
    Returns list of results for each thread count.
    """
    results = []
    
    for num_threads in thread_counts:
        print(f"\n  [{label}] Testing with {num_threads} threads...", flush=True)
        
        # Create stats collectors for each thread
        all_stats = [ThreadStats() for _ in range(num_threads)]
        
        # Run benchmark
        t_start = time.perf_counter()
        
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [
                executor.submit(
                    worker_thread,
                    client_factory,
                    thread_id,
                    n_pages,
                    n_reads_per_thread,
                    all_stats[thread_id]
                )
                for thread_id in range(num_threads)
            ]
            
            # Wait for completion
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"  [ERROR] Thread failed: {e}")
        
        total_time = time.perf_counter() - t_start
        
        # Aggregate results
        total_ops = 0
        total_hits = 0
        total_misses = 0
        total_errors = 0
        all_latencies = []
        
        for ts in all_stats:
            report = ts.get_report()
            if report:
                total_ops += report['total_ops']
                total_hits += report['hits']
                total_misses += report['misses']
                total_errors += report['errors']
                all_latencies.extend(ts.latencies)
        
        # Calculate aggregate metrics
        throughput_ops = total_ops / total_time if total_time > 0 else 0
        throughput_mb = (total_ops * PAGE_SIZE / (1024**2)) / total_time if total_time > 0 else 0
        
        sorted_lats = sorted(all_latencies) if all_latencies else []
        
        result = {
            'label': label,
            'threads': num_threads,
            'total_ops': total_ops,
            'total_time_s': total_time,
            'throughput_ops_sec': throughput_ops,
            'throughput_mb_s': throughput_mb,
            'hit_rate_pct': (total_hits / (total_hits + total_misses) * 100) if (total_hits + total_misses) > 0 else 0,
            'avg_latency_us': statistics.mean(all_latencies) if all_latencies else 0,
            'median_latency_us': sorted_lats[len(sorted_lats)//2] if sorted_lats else 0,
            'p90_latency_us': sorted_lats[int(len(sorted_lats)*0.90)] if sorted_lats else 0,
            'p99_latency_us': sorted_lats[int(len(sorted_lats)*0.99)] if sorted_lats else 0,
            'p999_latency_us': sorted_lats[min(int(len(sorted_lats)*0.999), len(sorted_lats)-1)] if sorted_lats else 0,
            'min_latency_us': min(all_latencies) if all_latencies else 0,
            'max_latency_us': max(all_latencies) if all_latencies else 0,
            'errors': total_errors,
            'per_thread_ops': total_ops / num_threads if num_threads > 0 else 0
        }
        
        results.append(result)
        
        print(f"    Threads: {num_threads}")
        print(f"    Throughput: {throughput_ops:.0f} ops/sec ({throughput_mb:.2f} MB/s)")
        print(f"    Hit Rate: {result['hit_rate_pct']:.2f}%")
        print(f"    Avg Latency: {result['avg_latency_us']:.2f} µs")
        print(f"    P99 Latency: {result['p99_latency_us']:.2f} µs")
        if total_errors > 0:
            print(f"    Errors: {total_errors}")
    
    return results


def print_results(all_results):
    """Print formatted results table."""
    print("\n" + SEP)
    print("  Multi-thread Benchmark Results")
    print(SEP)
    
    # Group by label
    labels = sorted(set(r['label'] for r in all_results))
    
    for label in labels:
        label_results = [r for r in all_results if r['label'] == label]
        
        print(f"\n  {label}")
        print("  " + "-" * 70)
        
        header = (
            f"  {'Threads':>8} | {'Throughput':>12} | {'Hit Rate':>9} | "
            f"{'Avg Lat':>9} | {'P99 Lat':>9} | {'P999 Lat':>10} | {'Errors':>7}"
        )
        print(header)
        print("  " + "-" * 70)
        
        for r in sorted(label_results, key=lambda x: x['threads']):
            print(f"  {r['threads']:>8} | {r['throughput_ops_sec']:>11.0f} | "
                  f"{r['hit_rate_pct']:>8.2f}% | {r['avg_latency_us']:>8.2f} µs | "
                  f"{r['p99_latency_us']:>8.2f} µs | {r['p999_latency_us']:>9.2f} µs | "
                  f"{r['errors']:>7}")
    
    print(SEP)
    
    # Scalability analysis
    print("\n  Scalability Analysis")
    print("  " + "-" * 70)
    
    for label in labels:
        label_results = [r for r in all_results if r['label'] == label]
        if len(label_results) < 2:
            continue
            
        # Compare 1-thread vs max-threads
        single = next((r for r in label_results if r['threads'] == 1), None)
        max_thread = max(label_results, key=lambda x: x['threads'])
        
        if single and max_thread:
            speedup = max_thread['throughput_ops_sec'] / single['throughput_ops_sec'] if single['throughput_ops_sec'] > 0 else 0
            efficiency = speedup / max_thread['threads'] * 100
            
            print(f"\n  {label}:")
            print(f"    1 thread:   {single['throughput_ops_sec']:.0f} ops/sec")
            print(f"    {max_thread['threads']} threads: {max_thread['throughput_ops_sec']:.0f} ops/sec")
            print(f"    Speedup: {speedup:.2f}x ({efficiency:.1f}% efficiency)")
            
            # Latency impact
            lat_increase = (max_thread['avg_latency_us'] - single['avg_latency_us']) / single['avg_latency_us'] * 100 if single['avg_latency_us'] > 0 else 0
            print(f"    Latency increase: {lat_increase:.1f}%")


def save_csv(results, output_dir='results'):
    """Save results to CSV file."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"multithread_benchmark_{timestamp}.csv"
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
    parser = argparse.ArgumentParser(description='HyperRAM Multi-thread Benchmark')
    parser.add_argument('--pages', type=int, default=2000, help='Number of pages per thread')
    parser.add_argument('--reads-per-thread', type=int, default=2000, help='Reads per thread')
    parser.add_argument('--threads', type=str, default='1,4,8,16', help='Thread counts to test (comma-separated)')
    parser.add_argument('--kernel-only', action='store_true', help='Test kernel mode only')
    parser.add_argument('--userspace-only', action='store_true', help='Test userspace only')
    parser.add_argument('--output', type=str, default='results', help='Output directory')
    args = parser.parse_args()
    
    thread_counts = [int(x.strip()) for x in args.threads.split(',')]
    
    print("\n" + SEP)
    print("  HyperRAM Multi-thread Benchmark")
    print(SEP)
    print(f"  Pages per thread: {args.pages} ({args.pages * PAGE_SIZE / 1024 / 1024:.1f} MB)")
    print(f"  Reads per thread: {args.reads_per_thread}")
    print(f"  Thread counts: {thread_counts}")
    print(SEP)
    
    all_results = []
    
    # Kernel mode benchmark
    if not args.userspace_only:
        print("\n[1/2] Kernel Mode Multi-thread Benchmark")
        print("-" * 50)
        
        kc = HyperRAMKernelClient()
        if kc.is_kernel_mode:
            # Pre-fill pages
            print(f"  Pre-filling {args.pages} pages...", flush=True)
            for i in range(args.pages):
                kc.write_page(i, bytes([i & 0xFF]) * PAGE_SIZE)
            
            def kernel_factory():
                return HyperRAMKernelClient()
            
            results = run_multithread_benchmark(
                kernel_factory,
                args.pages,
                args.reads_per_thread,
                thread_counts,
                "Kernel (Real NVMe)"
            )
            all_results.extend(results)
        else:
            print("  [SKIP] Kernel driver not loaded")
        
        kc.close()
    
    # Userspace benchmark
    if not args.kernel_only:
        print("\n[2/2] Userspace Mode Multi-thread Benchmark")
        print("-" * 50)
        
        try:
            pool_path = os.path.join(os.path.dirname(__file__), "..", "hyperram.pool")
            base_engine = HyperRAMEngine(ssd_pool_path=os.path.abspath(pool_path))
            base_engine.max_ram_cache_pages = 256
            
            # Pre-fill pages
            print(f"  Pre-filling {args.pages} pages...", flush=True)
            for i in range(args.pages):
                base_engine.write_page(i, bytes([i & 0xFF]) * PAGE_SIZE)
            base_engine.close()
            
            def user_factory():
                engine = HyperRAMEngine(ssd_pool_path=os.path.abspath(pool_path))
                engine.max_ram_cache_pages = 256
                return engine
            
            results = run_multithread_benchmark(
                user_factory,
                args.pages,
                args.reads_per_thread,
                thread_counts,
                "Userspace (mmap)"
            )
            all_results.extend(results)
        except Exception as e:
            print(f"  [ERROR] Userspace benchmark failed: {e}")
    
    # Print results
    if all_results:
        print_results(all_results)
        save_csv(all_results, args.output)
    else:
        print("\n  [ERROR] No benchmarks completed")
        sys.exit(1)
    
    print("\n" + SEP)
    print("  Benchmark Complete")
    print(SEP)


if __name__ == "__main__":
    main()