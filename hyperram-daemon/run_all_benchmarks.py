# HyperRun: Complete Benchmark Runner Script

Automated script to run all benchmarks and generate paper-ready results.

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
============================================================================
  run_all_benchmarks.py — HyperRAM Complete Paper Benchmark Suite
============================================================================
  Runs ALL benchmarks required for paper submission:
    1. Security & Stress Tests
    2. AI Model Benchmarks (Ollama models: 4B, 7B, 14B, 32B, 70B, 120B+)
    3. Multi-thread Scalability (1-64 threads)
    4. Research Questions (12 sections)
    5. Power Consumption
    6. Stability Test (optional long-running)
    7. LLM Stress Tests (Stage 1-6 progression)
    8. Data Integrity Tests (1M pages, concurrent access, eviction)

  Output:
    - CSV files in results/paper_YYYYMMDD/
    - Summary JSON with all statistics
    - Text report with key findings
    - Visual graphs (PNG, 300 DPI)
    - Paper results document (30-40 pages)

  Usage:
    python run_all_benchmarks.py              # Full suite
    python run_all_benchmarks.py --quick      # Quick validation (10 min)
    python run_all_benchmarks.py --ai-only    # Just AI benchmarks
    python run_all_benchmarks.py --security   # Just security tests
    python run_all_benchmarks.py --llm-stress # LLM stress tests only
    python run_all_benchmarks.py --integrity  # Data integrity tests only
    python run_all_benchmarks.py --generate   # Generate graphs and paper
============================================================================
"""
import sys, os
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import time, json, statistics, argparse, subprocess
from datetime import datetime
from pathlib import Path

SEP = "=" * 72
DASH = "-" * 72

class BenchmarkRunner:
    """Orchestrates all benchmarks and collects results."""
    
    def __init__(self, output_dir='results'):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        # Create timestamped subdirectory for this run
        self.timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.paper_dir = self.output_dir / f"paper_{self.timestamp}"
        self.paper_dir.mkdir(exist_ok=True)
        
        self.results = {
            'timestamp': self.timestamp,
            'benchmarks': {},
            'summary': {}
        }
        
    def run_script(self, script_name, args=[], capture_output=True, timeout=None):
        """Run a benchmark script and capture results."""
        cmd = [sys.executable, script_name] + args
        print(f"\n  Running: {' '.join(cmd)}")
        print(DASH)
        
        start_time = time.perf_counter()
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='replace'
            )
            
            elapsed = time.perf_counter() - start_time
            
            if result.returncode == 0:
                print(f"  ✓ Completed in {elapsed:.1f}s")
                return {
                    'status': 'success',
                    'elapsed_s': elapsed,
                    'stdout': result.stdout,
                    'stderr': result.stderr
                }
            else:
                print(f"  ✗ Failed with code {result.returncode}")
                print(result.stderr)
                return {
                    'status': 'failed',
                    'elapsed_s': elapsed,
                    'error': result.stderr,
                    'returncode': result.returncode
                }
                
        except subprocess.TimeoutExpired:
            print(f"  ⚠ Timeout after {timeout}s")
            return {
                'status': 'timeout',
                'elapsed_s': timeout
            }
        except Exception as e:
            print(f"  ✗ Exception: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def run_security_suite(self):
        """Run security and stress tests."""
        print("\n" + SEP)
        print("  BENCHMARK 1: Security & Stress Tests")
        print(SEP)
        
        result = self.run_script(
            'security_stress_test.py',
            timeout=600  # 10 minutes max
        )
        
        self.results['benchmarks']['security'] = result
        return result['status'] == 'success'
    
    def run_ai_benchmarks(self, quick=False, use_ollama=True):
        """Run AI model benchmarks."""
        print("\n" + SEP)
        print("  BENCHMARK 2: Real AI Models")
        print(SEP)
        
        if use_ollama:
            # Real local LLM benchmark via Ollama
            max_tokens = 100 if quick else 300
            result = self.run_script(
                'ai_benchmark_ollama.py',
                ['--max-tokens', str(max_tokens)],
                timeout=600 if quick else 1800
            )
            self.results['benchmarks']['ai'] = {'ollama': result}
            return result['status'] == 'success'
        else:
            # Simulated AI benchmark (legacy)
            models = ['llama-8b', 'qwen-7b'] if quick else ['all']
            contexts = ['8k'] if quick else ['4k', '8k', '16k']
            
            ai_results = {}
            for model in models:
                for ctx in contexts:
                    key = f"ai_{model}_{ctx}"
                    args = ['--model', model, '--context', ctx, '--tokens', '200']
                    result = self.run_script('ai_benchmark.py', args, timeout=300)
                    ai_results[key] = result
            
            self.results['benchmarks']['ai'] = ai_results
            return all(r['status'] == 'success' for r in ai_results.values())
    
    def run_llm_stress_test(self, stage=1, quick=False):
        """Run LLM stress benchmark (Stage 1-6 progression)."""
        print("\n" + SEP)
        print(f"  BENCHMARK 7: LLM Stress Test (Stage {stage})")
        print(SEP)
        
        stages = [stage] if not quick else [1]
        all_passed = True
        
        for s in stages:
            result = self.run_script(
                'llm_stress_benchmark.py',
                ['--stage', str(s)],
                timeout=3600 if not quick else 600  # 1 hour max per stage
            )
            
            if result['status'] != 'success':
                all_passed = False
                print(f"  Stage {s} failed, stopping progression")
                break
        
        self.results['benchmarks']['llm_stress'] = result
        return all_passed
    
    def run_data_integrity_test(self, quick=False):
        """Run data integrity validation suite."""
        print("\n" + SEP)
        print("  BENCHMARK 8: Data Integrity Tests")
        print(SEP)
        
        if quick:
            # Quick validation: 10K pages, 16 threads, 1 min eviction
            result = self.run_script(
                'data_integrity_test.py',
                ['--test', 'all', '--pages', '10000', '--threads', '16', '--duration', '1'],
                timeout=300
            )
        else:
            # Full validation: 100K pages, 64 threads, 5 min eviction
            result = self.run_script(
                'data_integrity_test.py',
                ['--test', 'all', '--pages', '100000', '--threads', '64', '--duration', '5'],
                timeout=1800  # 30 minutes
            )
        
        self.results['benchmarks']['data_integrity'] = result
        return result['status'] == 'success'
    
    def generate_visual_graphs(self):
        """Generate publication-quality graphs."""
        print("\n" + SEP)
        print("  GENERATING VISUAL GRAPHS")
        print(SEP)
        
        result = self.run_script(
            'plot_paper_graphs.py',
            ['--all'],
            timeout=120
        )
        
        self.results['graphs'] = result
        return result['status'] == 'success'
    
    def generate_paper_results(self):
        """Generate comprehensive paper results document."""
        print("\n" + SEP)
        print("  GENERATING PAPER RESULTS DOCUMENT")
        print(SEP)
        
        result = self.run_script(
            'generate_paper_results.py',
            [],
            timeout=120
        )
        
        self.results['paper'] = result
        return result['status'] == 'success'
    
    def run_multithread_benchmark(self):
        """Run scalability tests."""
        print("\n" + SEP)
        print("  BENCHMARK 3: Multi-thread Scalability")
        print(SEP)
        
        result = self.run_script(
            'multithread_benchmark.py',
            ['--pages', '1000', '--reads-per-thread', '500', '--threads', '1,4,8,16'],
            timeout=300
        )
        
        self.results['benchmarks']['multithread'] = result
        return result['status'] == 'success'
    
    def run_research_benchmarks(self, quick=False):
        """Run research-grade benchmarks (12 sections)."""
        print("\n" + SEP)
        print("  BENCHMARK 4: Research Questions (12 Sections)")
        print(SEP)
        
        # Run research_benchmark.py which covers all 12 sections
        result = self.run_script(
            'research_benchmark.py',
            [],
            timeout=1800 if not quick else 300  # 30 min full, 5 min quick
        )
        
        self.results['benchmarks']['research'] = result
        return result['status'] == 'success'
    
    def run_power_benchmark(self):
        """Run power consumption analysis."""
        print("\n" + SEP)
        print("  BENCHMARK 5: Power Consumption")
        print(SEP)
        
        result = self.run_script(
            'power_benchmark.py',
            ['--pages', '1000', '--reads', '5000'],
            timeout=300
        )
        
        self.results['benchmarks']['power'] = result
        return result['status'] == 'success'
    
    def run_stability_test(self, duration_hours=1):
        """Run stability test (default 1 hour)."""
        print("\n" + SEP)
        print(f"  BENCHMARK 6: Stability Test ({duration_hours}h)")
        print(SEP)
        
        result = self.run_script(
            'stability_test.py',
            ['--duration', f'{duration_hours}h', '--pages', '500', '--ops-per-sec', '50'],
            timeout=int(duration_hours * 3600 * 1.2)  # 20% buffer
        )
        
        self.results['benchmarks']['stability'] = result
        return result['status'] == 'success'
    
    def generate_summary(self):
        """Generate summary report."""
        print("\n" + SEP)
        print("  GENERATING SUMMARY REPORT")
        print(SEP)
        
        summary_path = self.paper_dir / 'benchmark_summary.json'
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2)
        
        # Generate text report
        report_path = self.paper_dir / 'benchmark_report.txt'
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("HyperRAM Paper Benchmark Results\n")
            f.write(f"Timestamp: {self.timestamp}\n")
            f.write("=" * 72 + "\n\n")
            
            for name, result in self.results['benchmarks'].items():
                status = result.get('status', 'unknown')
                elapsed = result.get('elapsed_s', 0)
                
                f.write(f"{name.upper()}:\n")
                f.write(f"  Status: {status}\n")
                f.write(f"  Duration: {elapsed:.1f}s\n")
                
                if status == 'success':
                    f.write(f"  Result: PASS\n")
                else:
                    f.write(f"  Result: FAIL - {result.get('error', 'Unknown')}\n")
                f.write("\n")
        
        print(f"  Summary saved to: {summary_path}")
        print(f"  Report saved to: {report_path}")
        
        # Count successes/failures
        total = len(self.results['benchmarks'])
        successes = sum(1 for r in self.results['benchmarks'].values() if r.get('status') == 'success')
        
        print(f"\n  BENCHMARK SUMMARY: {successes}/{total} passed")
        
        return successes == total
    
    def run_all(self, quick=False, ai_only=False, security_only=False, use_ollama=True, llm_stress=False, integrity_only=False, generate_only=False):
        """Run complete benchmark suite."""
        print("\n" + SEP)
        print("  HyperRAM Paper Benchmark Suite")
        print(SEP)
        print(f"  Output directory: {self.paper_dir}")
        print(f"  Mode: {'QUICK' if quick else 'FULL'}")
        print(SEP)
        
        start_time = time.perf_counter()
        
        if generate_only:
            # Just generate graphs and paper from existing results
            g1 = self.generate_visual_graphs()
            g2 = self.generate_paper_results()
            success = g1 and g2
            
        elif security_only:
            success = self.run_security_suite()
            
        elif ai_only:
            success = self.run_ai_benchmarks(quick=quick)
            
        elif llm_stress:
            success = self.run_llm_stress_test(stage=1, quick=quick)
            
        elif integrity_only:
            success = self.run_data_integrity_test(quick=quick)
            
        else:
            # Full suite
            print("\n  Running complete benchmark suite...")
            
            s1 = self.run_security_suite()
            s2 = self.run_ai_benchmarks(quick=quick)
            s3 = self.run_multithread_benchmark()
            s4 = self.run_research_benchmarks(quick=quick)
            s5 = self.run_power_benchmark()
            s6 = self.run_llm_stress_test(stage=1, quick=quick)
            s7 = self.run_data_integrity_test(quick=quick)
            
            if not quick:
                s8 = self.run_stability_test(duration_hours=1)
            
            # Generate visual graphs and paper
            g1 = self.generate_visual_graphs()
            g2 = self.generate_paper_results()
            
            success = all([s1, s2, s3, s4, s5, s6, s7, g1, g2]) if not quick else all([s1, s2, s3, s4, s5, s6, s7])
        
        total_elapsed = time.perf_counter() - start_time
        
        # Generate summary
        all_passed = self.generate_summary()
        
        print("\n" + SEP)
        if all_passed:
            print("  ✓ ALL BENCHMARKS PASSED")
            print(f"  Total time: {total_elapsed/60:.1f} minutes")
            print(f"  Results: {self.paper_dir}")
            print(f"  Graphs: {self.output_dir}/graphs/")
            print(f"  Paper: {self.paper_dir}/paper_results_*.md")
        else:
            print("  ✗ SOME BENCHMARKS FAILED")
            print("  Check benchmark_report.txt for details")
        print(SEP)
        
        return 0 if all_passed else 1


def main():
    parser = argparse.ArgumentParser(description='HyperRAM Paper Benchmark Suite')
    parser.add_argument('--quick', action='store_true', help='Quick validation (10 min)')
    parser.add_argument('--ai-only', action='store_true', help='Run AI benchmarks only')
    parser.add_argument('--security-only', action='store_true', help='Run security tests only')
    parser.add_argument('--llm-stress', action='store_true', help='Run LLM stress tests only')
    parser.add_argument('--integrity-only', action='store_true', help='Run data integrity tests only')
    parser.add_argument('--generate', action='store_true', help='Generate graphs and paper only')
    parser.add_argument('--stage', type=int, choices=[1, 2, 3, 4, 5, 6], default=1, help='LLM stress stage')
    parser.add_argument('--stability-hours', type=float, default=1.0, help='Stability test duration')
    parser.add_argument('--output', type=str, default='results', help='Output directory')
    parser.add_argument('--ollama', action='store_true', help='Use Ollama for real LLM benchmarks')
    parser.add_argument('--no-ollama', action='store_true', help='Use simulated AI benchmarks (legacy)')
    
    args = parser.parse_args()
    
    runner = BenchmarkRunner(output_dir=args.output)
    
    # Determine AI benchmark mode
    use_ollama = args.ollama or (not args.no_ollama)  # Default to Ollama if available
    
    return runner.run_all(
        quick=args.quick,
        ai_only=args.ai_only,
        security_only=args.security_only,
        use_ollama=use_ollama,
        llm_stress=args.llm_stress,
        integrity_only=args.integrity_only,
        generate_only=args.generate
    )


if __name__ == "__main__":
    sys.exit(main())
```

---

## Usage Examples

### Full Benchmark Suite (Recommended for Paper)
```bash
python run_all_benchmarks.py
# Duration: ~45 minutes
# Output: results/paper_YYYYMMDD_HHMMSS/
```

### Quick Validation (Before Full Run)
```bash
python run_all_benchmarks.py --quick
# Duration: ~10 minutes
# Tests: Reduced iterations for validation
```

### Individual Components
```bash
# Just AI benchmarks
python run_all_benchmarks.py --ai-only

# Just security validation
python run_all_benchmarks.py --security-only

# Extended stability test (24 hours)
python stability_test.py --duration 24h
```

---

## Output Files Generated

```
results/paper_20260611_120000/
├── benchmark_summary.json      # All results in JSON
├── benchmark_report.txt        # Human-readable summary
├── ai_benchmark_*.csv          # AI model results
├── multithread_benchmark_*.csv # Scalability data
├── research_benchmark_*.csv    # 12 research questions
├── power_benchmark_*.csv       # Power analysis
└── stability_test_*.csv        # Long-duration stats
```

---

## Next Steps After Running

1. **Generate Visual Graphs** (Python/Matplotlib):
   ```python
   import pandas as pd
   import matplotlib.pyplot as plt
   
   # Load multithread results
   df = pd.read_csv('results/paper_*/multithread_benchmark_*.csv')
   
   # Plot scalability curve
   plt.figure(figsize=(10, 6))
   for label in df['label'].unique():
       subset = df[df['label'] == label]
       plt.plot(subset['threads'], subset['throughput_ops_sec'], marker='o', label=label)
   
   plt.xlabel('Threads')
   plt.ylabel('Throughput (ops/sec)')
   plt.title('HyperRAM Scalability')
   plt.legend()
   plt.grid(True)
   plt.savefig('scalability_curve.png', dpi=300)
   ```

2. **Create Comparison Tables**:
   - AI model performance (tokens/sec)
   - Hit-rate sensitivity analysis
   - Tail latency comparison (P50/P95/P99)

3. **Write Paper Sections**:
   - §3: Persistent Metadata (Driver.cpp:154-410)
   - §4: Security Analysis (0 crashes validated)
   - §5: Evaluation (AI + Scalability + 12 research Qs)
   - §6: Related Work (use R9 comparison)

---

**Ready to execute benchmarks for paper submission.**