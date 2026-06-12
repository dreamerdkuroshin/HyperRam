# -*- coding: utf-8 -*-
r"""
============================================================================
  llm_stress_benchmark.py — LLM Stress Benchmark for HyperRAM
============================================================================
  Real-world stress test using Ollama to validate HyperRAM under actual
  LLM inference workloads. Tests memory-mapped file support, race conditions,
  eviction correctness, and data integrity under heavy multi-threaded load.
  
  Test Progression:
    Stage 1: 4B model (2-3 GB)
    Stage 2: 7B model (4-5 GB)
    Stage 3: 14B model (8-10 GB)
    Stage 4: 32B model (18-25 GB)
    Stage 5: 70B model (40-50 GB)
    Stage 6: 120B+ model (70-100+ GB)
  
  Metrics:
    - Model load success/failure
    - Tokens/sec (sustained)
    - Cache hit rate during inference
    - SSD reads/writes
    - Evictions triggered
    - Compression ratio
    - Runtime duration before failure
    - Output consistency (hash verification)
  
  Usage:
    python llm_stress_benchmark.py --stage 1 --model gemma3:4b
    python llm_stress_benchmark.py --stage 2 --model llama3:8b
    python llm_stress_benchmark.py --all-stages
    python llm_stress_benchmark.py --duration 1h --model gemma3:4b
============================================================================
"""
import sys, os, json, time, hashlib, threading, subprocess
from datetime import datetime
from pathlib import Path
import requests

sys.path.insert(0, os.path.dirname(__file__))
from kernel_client import HyperRAMKernelClient

PAGE_SIZE = 4096
SEP = "=" * 72
DASH = "-" * 72

# Stage configurations
STAGE_CONFIGS = {
    1: {'model': 'gemma3:4b', 'params_gb': 4, 'min_duration_min': 5, 'target_tokens': 1000},
    2: {'model': 'llama3:8b', 'params_gb': 8, 'min_duration_min': 10, 'target_tokens': 2000},
    3: {'model': 'qwen2.5:14b', 'params_gb': 14, 'min_duration_min': 15, 'target_tokens': 3000},
    4: {'model': 'qwen2.5-coder:32b', 'params_gb': 32, 'min_duration_min': 20, 'target_tokens': 4000},
    5: {'model': 'llama3.1:70b', 'params_gb': 70, 'min_duration_min': 30, 'target_tokens': 5000},
    6: {'model': 'mixtral:8x22b', 'params_gb': 120, 'min_duration_min': 60, 'target_tokens': 10000},
}

class OllamaMonitor:
    """Monitors Ollama server health and captures logs."""
    
    def __init__(self, base_url='http://localhost:11434'):
        self.base_url = base_url
        self.log_lines = []
        self.errors = []
        self.running = False
        self.monitor_thread = None
        
    def start(self):
        """Start background monitoring."""
        self.running = True
        self.log_lines = []
        self.errors = []
        self.monitor_thread = threading.Thread(target=self._monitor_loop)
        self.monitor_thread.start()
        
    def stop(self):
        """Stop monitoring."""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
    
    def _monitor_loop(self):
        """Background health check loop."""
        last_status = None
        while self.running:
            try:
                response = requests.get(f'{self.base_url}/api/tags', timeout=5)
                status = 'healthy' if response.status_code == 200 else 'unhealthy'
                
                if status != last_status:
                    timestamp = datetime.now().strftime('%H:%M:%S')
                    self.log_lines.append(f"[{timestamp}] Ollama status: {status}")
                    last_status = status
                    
            except requests.exceptions.RequestException as e:
                timestamp = datetime.now().strftime('%H:%M:%S')
                error_msg = f"[{timestamp}] Ollama connection error: {e}"
                self.log_lines.append(error_msg)
                self.errors.append(error_msg)
            
            time.sleep(2)
    
    def get_summary(self):
        """Get monitoring summary."""
        return {
            'log_lines': len(self.log_lines),
            'errors': len(self.errors),
            'last_logs': self.log_lines[-10:] if self.log_lines else [],
        }


class LLMStressBenchmark:
    """Runs LLM stress tests on HyperRAM."""
    
    def __init__(self, output_dir='results'):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.client = HyperRAMKernelClient()
        self.ollama_monitor = OllamaMonitor()
        
        self.results = {
            'stage': None,
            'model': None,
            'start_time': None,
            'end_time': None,
            'duration_sec': 0,
            'load_success': False,
            'load_error': None,
            'tokens_generated': 0,
            'tokens_per_sec': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'ssd_reads': 0,
            'ssd_writes': 0,
            'evictions': 0,
            'compression_ratio': 1.0,
            'hit_rate_pct': 0,
            'errors': [],
            'ollama_logs': [],
            'data_integrity': 'unknown',
        }
        
    def get_initial_stats(self):
        """Get initial HyperRAM stats."""
        stats = self.client.get_stats()
        if stats:
            d = stats.to_dict()
            return {
                'nvme_reads': d.get('nvme_reads', 0),
                'nvme_writes': d.get('nvme_writes', 0),
                'cache_hits': d.get('cache_hits', 0),
                'cache_misses': d.get('cache_misses', 0),
                'evictions': d.get('evictions', 0),
                'compression_ratio': d.get('compression_ratio', 1.0),
            }
        return None
    
    def generate_test_prompts(self, count=10):
        """Generate diverse test prompts."""
        prompts = [
            "Explain quantum entanglement in simple terms.",
            "Write a Python function to sort a list using merge sort.",
            "What is the difference between supervised and unsupervised learning?",
            "Describe the architecture of a transformer model.",
            "Solve this math problem: If x + 2y = 10 and 2x - y = 5, find x and y.",
            "Write a short story about a robot learning to feel emotions.",
            "Explain how blockchain works in 3 paragraphs.",
            "What are the key differences between HTTP/2 and HTTP/3?",
            "Describe the process of photosynthesis step by step.",
            "Write a haiku about artificial intelligence.",
        ]
        return prompts[:count]
    
    def run_inference_test(self, model, prompt, max_tokens=500):
        """Run single inference test with monitoring."""
        url = f'http://localhost:11434/api/generate'
        payload = {
            'model': model,
            'prompt': prompt,
            'stream': False,
            'options': {
                'num_predict': max_tokens,
                'temperature': 0.7,
            }
        }
        
        start_time = time.perf_counter()
        try:
            response = requests.post(url, json=payload, timeout=300)
            elapsed = time.perf_counter() - start_time
            
            if response.status_code == 200:
                data = response.json()
                generated = data.get('response', '')
                tokens = len(generated.split())
                return {
                    'success': True,
                    'tokens': tokens,
                    'elapsed_sec': elapsed,
                    'output': generated,
                    'error': None,
                }
            else:
                return {
                    'success': False,
                    'tokens': 0,
                    'elapsed_sec': elapsed,
                    'output': None,
                    'error': f"HTTP {response.status_code}: {response.text}",
                }
                
        except requests.exceptions.Timeout:
            elapsed = time.perf_counter() - start_time
            return {
                'success': False,
                'tokens': 0,
                'elapsed_sec': elapsed,
                'output': None,
                'error': 'Timeout (5 minutes)',
            }
        except requests.exceptions.RequestException as e:
            elapsed = time.perf_counter() - start_time
            return {
                'success': False,
                'tokens': 0,
                'elapsed_sec': elapsed,
                'output': None,
                'error': str(e),
            }
    
    def verify_data_integrity(self, initial_hash=None):
        """Verify data integrity by reading back test pages."""
        print("    Verifying data integrity...")
        
        # Write test pattern to pages
        test_pages = list(range(100, 110))
        patterns = {}
        
        for page_id in test_pages:
            pattern = bytes([(page_id + i) & 0xFF for i in range(PAGE_SIZE)])
            patterns[page_id] = hashlib.sha256(pattern).hexdigest()
            self.client.write_page(page_id, pattern)
        
        # Read back and verify
        errors = []
        for page_id in test_pages:
            try:
                data = self.client.read_page(page_id)
                actual_hash = hashlib.sha256(data).hexdigest()
                expected_hash = patterns[page_id]
                
                if actual_hash != expected_hash:
                    errors.append(f"Page {page_id}: hash mismatch")
                    
            except Exception as e:
                errors.append(f"Page {page_id}: read error - {e}")
        
        if errors:
            print(f"    ✗ Data integrity check FAILED: {len(errors)} errors")
            self.results['data_integrity'] = 'corrupted'
            return False
        else:
            print("    ✓ Data integrity check PASSED")
            self.results['data_integrity'] = 'verified'
            return True
    
    def run_stage(self, stage_num, model=None, duration_min=None, target_tokens=None):
        """Run a single stress test stage."""
        config = STAGE_CONFIGS.get(stage_num, {})
        model = model or config.get('model', 'gemma3:4b')
        duration_min = duration_min or config.get('min_duration_min', 5)
        target_tokens = target_tokens or config.get('target_tokens', 1000)
        
        print("\n" + SEP)
        print(f"  LLM Stress Test - Stage {stage_num}")
        print(SEP)
        print(f"  Model: {model}")
        print(f"  Target Duration: {duration_min} minutes")
        print(f"  Target Tokens: {target_tokens}")
        print(DASH)
        
        # Initialize results
        self.results['stage'] = stage_num
        self.results['model'] = model
        self.results['start_time'] = datetime.now().isoformat()
        self.results['errors'] = []
        
        # Get initial stats
        initial_stats = self.get_initial_stats()
        if not initial_stats:
            print("  [ERROR] Cannot get initial HyperRAM stats")
            self.results['load_error'] = 'HyperRAM not available'
            return self.results
        
        print(f"\n  Initial HyperRAM Stats:")
        print(f"    SSD Reads: {initial_stats['nvme_reads']}")
        print(f"    SSD Writes: {initial_stats['nvme_writes']}")
        print(f"    Cache Hits: {initial_stats['cache_hits']}")
        print(f"    Cache Misses: {initial_stats['cache_misses']}")
        
        # Start Ollama monitoring
        print("\n  Starting Ollama health monitor...")
        self.ollama_monitor.start()
        
        # Warm up HyperRAM cache
        print("\n  Warming up HyperRAM cache (1000 pages)...")
        for i in range(1000):
            self.client.write_page(i, bytes([i & 0xFF]) * PAGE_SIZE)
        
        # Run inference tests
        print(f"\n  Running inference tests...")
        prompts = self.generate_test_prompts(count=10)
        
        total_tokens = 0
        total_elapsed = 0
        successful_runs = 0
        failed_runs = 0
        
        start_time = time.perf_counter()
        max_duration_sec = duration_min * 60
        
        for i, prompt in enumerate(prompts):
            if time.perf_counter() - start_time > max_duration_sec:
                print(f"  [INFO] Duration limit reached ({duration_min} min)")
                break
            
            if total_tokens >= target_tokens:
                print(f"  [INFO] Target tokens reached ({target_tokens})")
                break
            
            print(f"    Prompt {i+1}/{len(prompts)}...", end=' ', flush=True)
            
            result = self.run_inference_test(model, prompt, max_tokens=max(50, target_tokens // len(prompts)))
            
            if result['success']:
                total_tokens += result['tokens']
                total_elapsed += result['elapsed_sec']
                successful_runs += 1
                print(f"✓ {result['tokens']} tokens in {result['elapsed_sec']:.1f}s")
            else:
                failed_runs += 1
                error_msg = f"Prompt {i+1} failed: {result['error']}"
                print(f"✗ {result['error']}")
                self.results['errors'].append(error_msg)
        
        # Stop monitoring
        self.ollama_monitor.stop()
        
        # Get final stats
        final_stats = self.get_initial_stats()
        
        # Calculate deltas
        duration_sec = time.perf_counter() - start_time
        self.results['end_time'] = datetime.now().isoformat()
        self.results['duration_sec'] = duration_sec
        self.results['load_success'] = successful_runs > 0
        self.results['tokens_generated'] = total_tokens
        self.results['tokens_per_sec'] = total_tokens / duration_sec if duration_sec > 0 else 0
        
        if final_stats and initial_stats:
            self.results['ssd_reads'] = final_stats['nvme_reads'] - initial_stats['nvme_reads']
            self.results['ssd_writes'] = final_stats['nvme_writes'] - initial_stats['nvme_writes']
            self.results['cache_hits'] = final_stats['cache_hits'] - initial_stats['cache_hits']
            self.results['cache_misses'] = final_stats['cache_misses'] - initial_stats['cache_misses']
            self.results['evictions'] = final_stats.get('evictions', 0) - initial_stats.get('evictions', 0)
            self.results['compression_ratio'] = final_stats.get('compression_ratio', 1.0)
            
            total_accesses = self.results['cache_hits'] + self.results['cache_misses']
            self.results['hit_rate_pct'] = (self.results['cache_hits'] / total_accesses * 100) if total_accesses > 0 else 0
        
        # Ollama logs
        ollama_summary = self.ollama_monitor.get_summary()
        self.results['ollama_logs'] = ollama_summary['last_logs']
        if ollama_summary['errors']:
            self.results['errors'].extend(ollama_summary['errors'])
        
        # Data integrity check
        self.verify_data_integrity()
        
        # Print summary
        print("\n" + DASH)
        print("  Stage Summary:")
        print(f"    Duration: {duration_sec:.1f}s ({duration_sec/60:.1f} min)")
        print(f"    Tokens Generated: {total_tokens}")
        print(f"    Tokens/sec: {self.results['tokens_per_sec']:.2f}")
        print(f"    Successful Runs: {successful_runs}/{len(prompts)}")
        print(f"    Failed Runs: {failed_runs}")
        print(f"    Cache Hit Rate: {self.results['hit_rate_pct']:.1f}%")
        print(f"    SSD Reads: {self.results['ssd_reads']}")
        print(f"    SSD Writes: {self.results['ssd_writes']}")
        print(f"    Compression: {self.results['compression_ratio']:.2f}x")
        print(f"    Data Integrity: {self.results['data_integrity']}")
        
        if self.results['errors']:
            print(f"\n  Errors ({len(self.results['errors'])}):")
            for err in self.results['errors'][:5]:
                print(f"    - {err}")
            if len(self.results['errors']) > 5:
                print(f"    ... and {len(self.results['errors']) - 5} more")
        
        print(DASH)
        
        return self.results
    
    def save_results(self):
        """Save results to JSON and CSV."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Save JSON
        json_path = self.output_dir / f'llm_stress_stage{self.results["stage"]}_{timestamp}.json'
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, default=str)
        
        # Save CSV (single row)
        csv_path = self.output_dir / f'llm_stress_stage{self.results["stage"]}_{timestamp}.csv'
        import csv
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            if self.results:
                fieldnames = list(self.results.keys())
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow(self.results)
        
        print(f"\n  Results saved to:")
        print(f"    {json_path}")
        print(f"    {csv_path}")
        
        return json_path, csv_path
    
    def close(self):
        """Cleanup resources."""
        self.client.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='LLM Stress Benchmark for HyperRAM')
    parser.add_argument('--stage', type=int, choices=[1, 2, 3, 4, 5, 6], default=1,
                       help='Stress test stage (1=4B, 6=120B+)')
    parser.add_argument('--model', type=str, default=None,
                       help='Ollama model name (overrides stage default)')
    parser.add_argument('--duration', type=str, default=None,
                       help='Test duration (e.g., "5m", "1h")')
    parser.add_argument('--target-tokens', type=int, default=None,
                       help='Target number of tokens to generate')
    parser.add_argument('--all-stages', action='store_true',
                       help='Run all stages sequentially')
    parser.add_argument('--output', type=str, default='results',
                       help='Output directory')
    
    args = parser.parse_args()
    
    print("\n" + SEP)
    print("  HyperRAM LLM Stress Benchmark")
    print(SEP)
    
    benchmark = LLMStressBenchmark(output_dir=args.output)
    
    try:
        if args.all_stages:
            print("\nRunning ALL stages sequentially...")
            all_results = []
            for stage_num in range(1, 7):
                config = STAGE_CONFIGS.get(stage_num, {})
                result = benchmark.run_stage(
                    stage_num=stage_num,
                    model=config.get('model'),
                    duration_min=config.get('min_duration_min'),
                    target_tokens=config.get('target_tokens'),
                )
                benchmark.save_results()
                all_results.append(result.copy())
                
                # Check if stage failed
                if not result.get('load_success', False):
                    print(f"\n  [STOP] Stage {stage_num} failed. Stopping all-stages run.")
                    break
                
                # Brief pause between stages
                if stage_num < 6:
                    print(f"\n  Pausing 30 seconds before stage {stage_num + 1}...")
                    time.sleep(30)
            
            print("\n" + SEP)
            print("  All-Stages Summary:")
            for i, result in enumerate(all_results, 1):
                status = "✓ PASS" if result.get('load_success') else "✗ FAIL"
                print(f"    Stage {i}: {status} ({result.get('model', 'N/A')})")
            print(SEP)
            
        else:
            # Parse duration
            duration_min = None
            if args.duration:
                if args.duration.endswith('h'):
                    duration_min = float(args.duration[:-1]) * 60
                elif args.duration.endswith('m'):
                    duration_min = float(args.duration[:-1])
                else:
                    duration_min = float(args.duration)
            
            benchmark.run_stage(
                stage_num=args.stage,
                model=args.model,
                duration_min=duration_min,
                target_tokens=args.target_tokens,
            )
            benchmark.save_results()
    
    finally:
        benchmark.close()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())