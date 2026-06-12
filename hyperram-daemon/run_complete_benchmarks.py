# -*- coding: utf-8 -*-
r"""
============================================================================
  run_complete_benchmarks.py — HyperRAM Complete Benchmark Suite
============================================================================
  Runs ALL benchmarks with time estimates and generates 30-40 page paper.
  
  TIME ESTIMATES:
    - Security Test: 2-3 minutes
    - AI Benchmark (all models): 15-30 minutes (depends on model count)
    - Multithread Benchmark: 5-10 minutes
    - Research Benchmark: 10-20 minutes
    - Power Benchmark: 3-5 minutes
    - Stability Test (optional): 1-24 hours
    - Paper Generation: 1-2 minutes
  
  TOTAL: 35-70 minutes (without stability test)
         1.5-25 hours (with stability test)
  
  Usage:
    python run_complete_benchmarks.py              # Full suite (~1 hour)
    python run_complete_benchmarks.py --quick      # Quick run (~15 min)
    python run_complete_benchmarks.py --ai-only    # Just AI (~20 min)
    python run_complete_benchmarks.py --stability  # Include 1h stability
============================================================================
"""
import sys, os, time, subprocess, json
from datetime import datetime
from pathlib import Path

SEP = "=" * 72
DASH = "-" * 72

class BenchmarkRunner:
    """Orchestrates complete benchmark suite with timing."""
    
    def __init__(self, quick=False, include_stability=False):
        self.quick = quick
        self.include_stability = include_stability
        self.results_dir = Path('results')
        self.results_dir.mkdir(exist_ok=True)
        
        # Timestamp for this run
        self.timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.paper_dir = self.results_dir / f"paper_{self.timestamp}"
        self.paper_dir.mkdir(exist_ok=True)
        
        self.results = {
            'timestamp': self.timestamp,
            'benchmarks': {},
            'timing': {}
        }
        
    def run_script(self, script_name, args=[], timeout=None, description=""):
        """Run a benchmark script with timing."""
        cmd = [sys.executable, script_name] + args
        print(f"\n{DASH}")
        print(f"  Running: {description}")
        print(f"  Command: {' '.join(cmd)}")
        print(DASH)
        
        start_time = time.perf_counter()
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='replace',
                cwd=os.path.dirname(__file__) or '.'
            )
            
            elapsed = time.perf_counter() - start_time
            mins, secs = divmod(int(elapsed), 60)
            
            if result.returncode == 0:
                print(f"\n  [PASS] Completed in {mins}m {secs}s")
                status = 'success'
            else:
                print(f"\n  [FAIL] Failed with code {result.returncode}")
                status = 'failed'
            
            # Save output logs
            log_prefix = script_name.replace('.py', '')
            stdout_path = self.paper_dir / f"{log_prefix}_stdout.log"
            stderr_path = self.paper_dir / f"{log_prefix}_stderr.log"
            
            with open(stdout_path, 'w', encoding='utf-8') as f:
                f.write(result.stdout)
            with open(stderr_path, 'w', encoding='utf-8') as f:
                f.write(result.stderr)
            
            return {
                'status': status,
                'elapsed_s': elapsed,
                'elapsed_formatted': f"{mins}m {secs}s",
                'stdout_lines': len(result.stdout.split('\n')),
                'stderr_lines': len(result.stderr.split('\n')),
            }
            
        except subprocess.TimeoutExpired as e:
            elapsed = time.perf_counter() - start_time
            print(f"\n  [TIMEOUT] Timeout after {timeout}s ({elapsed:.0f}s actual)")
            return {
                'status': 'timeout',
                'elapsed_s': elapsed,
                'error': f'Timeout after {timeout}s'
            }
        except Exception as e:
            elapsed = time.perf_counter() - start_time
            print(f"\n  ✗ Exception: {e}")
            return {
                'status': 'error',
                'elapsed_s': elapsed,
                'error': str(e)
            }
    
    def run_security_suite(self):
        """Run security and stress tests. Time: 2-3 min."""
        print("\n" + SEP)
        print("  BENCHMARK 1/5: Security & Stress Tests")
        print("  Estimated time: 2-3 minutes")
        print(SEP)
        
        result = self.run_script(
            'security_stress_test.py',
            timeout=600,  # 10 min max
            description="Security validation (IOCTL, race conditions, fuzzing)"
        )
        
        self.results['benchmarks']['security'] = result
        self.results['timing']['security'] = result.get('elapsed_s', 0)
        return result['status'] == 'success'
    
    def run_ai_benchmarks(self):
        """Run AI benchmarks with all Ollama models. Time: 15-30 min."""
        print("\n" + SEP)
        print("  BENCHMARK 2/5: Real AI Models (Ollama)")
        if self.quick:
            print("  Mode: QUICK (50 tokens per model)")
            print("  Estimated time: 5-10 minutes")
        else:
            print("  Mode: FULL (200 tokens per model)")
            print("  Estimated time: 15-30 minutes")
        print(SEP)
        
        # First list available models (with longer timeout)
        print("\n  Detecting available Ollama models...")
        try:
            list_result = subprocess.run(
                [sys.executable, 'ai_benchmark_ollama.py', '--list-models'],
                capture_output=True,
                text=True,
                timeout=60,  # Increased timeout
                cwd=os.path.dirname(__file__) or '.'
            )
            print(list_result.stdout)
            if list_result.returncode != 0:
                print("  [WARNING] Could not list models, proceeding anyway...")
        except subprocess.TimeoutExpired:
            print("  [WARNING] Ollama list timeout, proceeding with default models...")
        except Exception as e:
            print(f"  [WARNING] Ollama check failed: {e}")
            print("  Make sure Ollama is running: `ollama serve`")
        
        # Run benchmark
        max_tokens = 50 if self.quick else 200
        timeout = 1200 if self.quick else 3600  # Increased timeouts
        
        result = self.run_script(
            'ai_benchmark_ollama.py',
            ['--all-models', '--max-tokens', str(max_tokens)],
            timeout=timeout,
            description=f"AI benchmarks (all models, {max_tokens} tokens)"
        )
        
        self.results['benchmarks']['ai'] = result
        self.results['timing']['ai'] = result.get('elapsed_s', 0)
        return result['status'] == 'success'
    
    def run_multithread_benchmark(self):
        """Run scalability tests. Time: 5-10 min."""
        print("\n" + SEP)
        print("  BENCHMARK 3/5: Multi-thread Scalability")
        if self.quick:
            print("  Mode: QUICK (1, 4, 8 threads only)")
            print("  Estimated time: 3-5 minutes")
        else:
            print("  Mode: FULL (1, 4, 8, 16, 64 threads)")
            print("  Estimated time: 5-10 minutes")
        print(SEP)
        
        threads = '1,4,8' if self.quick else '1,4,8,16,64'
        
        result = self.run_script(
            'multithread_benchmark.py',
            ['--threads', threads, '--pages', '1000', '--reads-per-thread', '500'],
            timeout=900,  # 15 min max
            description=f"Scalability test ({threads})"
        )
        
        self.results['benchmarks']['multithread'] = result
        self.results['timing']['multithread'] = result.get('elapsed_s', 0)
        return result['status'] == 'success'
    
    def run_research_benchmarks(self):
        """Run research-grade benchmarks. Time: 10-20 min."""
        print("\n" + SEP)
        print("  BENCHMARK 4/5: Research Questions (R1-R12)")
        if self.quick:
            print("  Mode: QUICK (reduced iterations)")
            print("  Estimated time: 5 minutes")
        else:
            print("  Mode: FULL (all 12 research questions)")
            print("  Estimated time: 10-20 minutes")
        print(SEP)
        
        result = self.run_script(
            'research_benchmark.py',
            [],
            timeout=1800 if not self.quick else 600,
            description="Research benchmark suite (12 questions)"
        )
        
        self.results['benchmarks']['research'] = result
        self.results['timing']['research'] = result.get('elapsed_s', 0)
        return result['status'] == 'success'
    
    def run_power_benchmark(self):
        """Run power analysis. Time: 3-5 min."""
        print("\n" + SEP)
        print("  BENCHMARK 5/5: Power Consumption Analysis")
        print("  Estimated time: 3-5 minutes")
        print(SEP)
        
        result = self.run_script(
            'power_benchmark.py',
            ['--pages', '1000', '--reads', '5000'],
            timeout=600,
            description="Power efficiency benchmark"
        )
        
        self.results['benchmarks']['power'] = result
        self.results['timing']['power'] = result.get('elapsed_s', 0)
        return result['status'] == 'success'
    
    def run_stability_test(self, duration_hours=1):
        """Run stability test. Time: 1-24 hours."""
        print("\n" + SEP)
        print(f"  BENCHMARK 6/5: Stability Test ({duration_hours}h)")
        print("  Estimated time: " + f"{duration_hours} hours")
        print(SEP)
        
        result = self.run_script(
            'stability_test.py',
            ['--duration', f'{duration_hours}h'],
            timeout=int(duration_hours * 3600 * 1.2),
            description=f"Long-duration stability ({duration_hours}h)"
        )
        
        self.results['benchmarks']['stability'] = result
        self.results['timing']['stability'] = result.get('elapsed_s', 0)
        return result['status'] == 'success'
    
    def generate_paper(self):
        """Generate 30-40 page paper results. Time: 1-2 min."""
        print("\n" + SEP)
        print("  Generating Paper Results Document")
        print("  Estimated time: 1-2 minutes")
        print(SEP)
        
        result = self.run_script(
            'generate_paper_results.py',
            [],
            timeout=300,
            description="Paper results generation"
        )
        
        self.results['benchmarks']['paper_generation'] = result
        self.results['timing']['paper_generation'] = result.get('elapsed_s', 0)
        return result['status'] == 'success'
    
    def print_summary(self):
        """Print comprehensive summary."""
        print("\n" + SEP)
        print("  BENCHMARK SUITE SUMMARY")
        print(SEP)
        
        total_time = sum(self.results['timing'].values())
        hours, remainder = divmod(int(total_time), 3600)
        mins, secs = divmod(remainder, 60)
        
        print(f"\n  Total Execution Time: {hours}h {mins}m {secs}s\n")
        
        print(f"  {'Benchmark':<25} | {'Status':<10} | {'Duration':<12}")
        print(f"  {'-'*25}-+-{'-'*10}-+-{'-'*12}")
        
        for name, result in self.results['benchmarks'].items():
            status = "[PASS]" if result.get('status') == 'success' else "[FAIL]"
            duration = result.get('elapsed_formatted', f"{result.get('elapsed_s', 0):.0f}s")
            print(f"  {name:<25} | {status:<10} | {duration:<12}")
        
        print("\n" + SEP)
        
        # Count successes
        total = len(self.results['benchmarks'])
        successes = sum(1 for r in self.results['benchmarks'].values() if r.get('status') == 'success')
        
        print(f"\n  Results: {successes}/{total} benchmarks passed")
        print(f"  Output directory: {self.paper_dir}")
        print(f"  Log files: {len(list(self.paper_dir.glob('*.log')))} files")
        
        if successes == total:
            print("\n  [SUCCESS] ALL BENCHMARKS PASSED")
            print(f"  Paper results ready in: {self.paper_dir}")
        else:
            print("\n  [FAILURE] SOME BENCHMARKS FAILED")
            print("  Check log files for details")
        
        print(SEP)
        
        return successes == total
    
    def run_all(self):
        """Run complete benchmark suite."""
        print("\n" + SEP)
        print("  HyperRAM Complete Benchmark Suite")
        print(f"  Timestamp: {self.timestamp}")
        print(f"  Mode: {'QUICK' if self.quick else 'FULL'}")
        if self.include_stability:
            print("  Stability Test: INCLUDED (1 hour)")
        else:
            print("  Stability Test: SKIPPED (use --stability to include)")
        print(SEP)
        
        start_time = time.perf_counter()
        
        # Run benchmarks in order
        s1 = self.run_security_suite()
        s2 = self.run_ai_benchmarks()
        s3 = self.run_multithread_benchmark()
        s4 = self.run_research_benchmarks()
        s5 = self.run_power_benchmark()
        
        s6 = True
        if self.include_stability:
            s6 = self.run_stability_test(duration_hours=1)
        
        # Generate paper
        s7 = self.generate_paper()
        
        # Print summary
        all_passed = all([s1, s2, s3, s4, s5, s6, s7])
        self.print_summary()
        
        total_elapsed = time.perf_counter() - start_time
        hours, remainder = divmod(int(total_elapsed), 3600)
        mins, secs = divmod(remainder, 60)
        
        print(f"\n  Wall-clock time: {hours}h {mins}m {secs}s")
        print(f"  Results saved to: {self.paper_dir}")
        print(SEP)
        
        return 0 if all_passed else 1


def main():
    import argparse
    parser = argparse.ArgumentParser(description='HyperRAM Complete Benchmark Suite')
    parser.add_argument('--quick', action='store_true', help='Quick validation (~15 min)')
    parser.add_argument('--stability', action='store_true', help='Include 1h stability test')
    parser.add_argument('--ai-only', action='store_true', help='Run AI benchmarks only')
    parser.add_argument('--security-only', action='store_true', help='Run security tests only')
    args = parser.parse_args()
    
    runner = BenchmarkRunner(quick=args.quick, include_stability=args.stability)
    
    if args.ai_only:
        success = runner.run_ai_benchmarks()
        runner.print_summary()
        return 0 if success else 1
    elif args.security_only:
        success = runner.run_security_suite()
        runner.print_summary()
        return 0 if success else 1
    else:
        return runner.run_all()


if __name__ == "__main__":
    sys.exit(main())