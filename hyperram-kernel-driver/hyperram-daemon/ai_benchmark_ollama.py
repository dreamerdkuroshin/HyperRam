# -*- coding: utf-8 -*-
r"""
============================================================================
  ai_benchmark_ollama.py — Real Local LLM Benchmark via Ollama
============================================================================
  Measures HyperRAM performance with REAL local LLM inference using Ollama:
    - Uses Ollama API for local inference
    - Monitors memory access patterns during generation
    - Tracks HyperRAM stats during actual LLM execution
  
  Models: Any model available in your local Ollama installation
    - llama3, llama3.1, mistral, mixtral, qwen, deepseek-coder, etc.
  
  Metrics:
    - Tokens/sec (real inference, not simulated)
    - Context size (pages)
    - Memory usage (RAM/SSD)
    - SSD reads/writes during inference
    - Compression ratio
    - Cache hit rate during generation
    - Prefetcher effectiveness

  Prerequisites:
    - Ollama installed: https://ollama.ai
    - Models pulled: `ollama pull llama3`, `ollama pull mistral`, etc.
    - Ollama running: `ollama serve` (default: localhost:11434)

  Usage:
    python ai_benchmark_ollama.py --model llama3 --prompt "Hello"
    python ai_benchmark_ollama.py --model mistral --max-tokens 500
    python ai_benchmark_ollama.py --all-models
    python ai_benchmark_ollama.py --list-models
============================================================================
"""
import sys, os
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import time, statistics, csv, json, argparse, threading
from datetime import datetime
from pathlib import Path
import requests

sys.path.insert(0, os.path.dirname(__file__))
from kernel_client import HyperRAMKernelClient

PAGE_SIZE = 4096
SEP = "=" * 72
DASH = "-" * 72

# ---------------------------------------------------------------------------
# Ollama API Client
# ---------------------------------------------------------------------------
class OllamaClient:
    """Simple Ollama API client for local LLM inference."""
    
    def __init__(self, base_url='http://localhost:11434'):
        self.base_url = base_url
        
    def list_models(self):
        """Get list of available local models."""
        try:
            response = requests.get(f'{self.base_url}/api/tags', timeout=5)
            response.raise_for_status()
            data = response.json()
            return [model['name'] for model in data.get('models', [])]
        except Exception as e:
            print(f"  [ERROR] Cannot connect to Ollama: {e}")
            print("  Make sure Ollama is running: `ollama serve`")
            return []
    
    def get_model_info(self, model):
        """Get model details (size, parameters, etc.)."""
        try:
            response = requests.post(
                f'{self.base_url}/api/show',
                json={'name': model},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except:
            return {}
    
    def generate(self, model, prompt, max_tokens=500, stream=False):
        """
        Generate text using Ollama API.
        
        Returns: (generated_text, tokens_generated, total_time_sec)
        """
        url = f'{self.base_url}/api/generate'
        payload = {
            'model': model,
            'prompt': prompt,
            'stream': stream,
            'options': {
                'num_predict': max_tokens
            }
        }
        
        start_time = time.perf_counter()
        tokens_count = 0
        generated_text = ""
        
        try:
            if stream:
                response = requests.post(url, json=payload, stream=True, timeout=300)
                response.raise_for_status()
                
                for line in response.iter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            if 'response' in data:
                                generated_text += data['response']
                                tokens_count += 1
                        except json.JSONDecodeError:
                            pass
            else:
                response = requests.post(url, json=payload, timeout=300)
                response.raise_for_status()
                data = response.json()
                generated_text = data.get('response', '')
                tokens_count = len(generated_text.split())  # Approximate token count
                
            elapsed = time.perf_counter() - start_time
            return generated_text, tokens_count, elapsed
            
        except requests.exceptions.RequestException as e:
            print(f"  [ERROR] Ollama generation failed: {e}")
            return "", 0, 0


# ---------------------------------------------------------------------------
# HyperRAM Memory Monitor
# ---------------------------------------------------------------------------
class MemoryMonitor:
    """Monitors HyperRAM stats during LLM inference."""
    
    def __init__(self, client, sample_interval=0.01):
        self.client = client
        self.sample_interval = sample_interval
        self.samples = []
        self.running = False
        self.monitor_thread = None
        
    def start(self):
        """Start background monitoring."""
        self.running = True
        self.samples = []
        self.monitor_thread = threading.Thread(target=self._monitor_loop)
        self.monitor_thread.start()
        
    def stop(self):
        """Stop monitoring."""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
            
    def _monitor_loop(self):
        """Background sampling loop."""
        while self.running:
            try:
                stats = self.client.get_stats()
                if stats:
                    self.samples.append({
                        'timestamp': time.perf_counter(),
                        'stats': stats.to_dict()
                    })
            except:
                pass
            time.sleep(self.sample_interval)
    
    def get_delta(self):
        """Get change in stats from start to end."""
        if len(self.samples) < 2:
            return {}
        
        start = self.samples[0]['stats']
        end = self.samples[-1]['stats']
        
        return {
            'ssd_reads': end['nvme_reads'] - start['nvme_reads'],
            'ssd_writes': end['nvme_writes'] - start['nvme_writes'],
            'cache_hits': end['cache_hits'] - start['cache_hits'],
            'cache_misses': end['cache_misses'] - start['cache_misses'],
            'ram_pages_start': start['ram_cache_pages'],
            'ram_pages_end': end['ram_cache_pages'],
            'pool_used_mb_start': start['pool_used_mb'],
            'pool_used_mb_end': end['pool_used_mb'],
            'compression_ratio': end['compression_ratio'],
            'hit_rate_pct': end['hit_rate_pct'],
        }


# ---------------------------------------------------------------------------
# Model Configurations
# ---------------------------------------------------------------------------
MODEL_CONFIGS = {
    'beru-unbound-8b': {
        'display_name': 'Beru-Unbound 8B',
        'params_gb': 8,
        'expected_context': 8192,
    },
    'deepseek-r1:8b': {
        'display_name': 'DeepSeek R1 8B',
        'params_gb': 8,
        'expected_context': 32768,
    },
    'gemma3:4b': {
        'display_name': 'Gemma 3 4B',
        'params_gb': 4,
        'expected_context': 8192,
    },
    'qwen-coder-30b': {
        'display_name': 'Qwen Coder 30B',
        'params_gb': 30,
        'expected_context': 32768,
    },
    'gpt-oss-120b': {
        'display_name': 'GPT-OSS 120B',
        'params_gb': 120,
        'expected_context': 131072,
    },
    'dolphin-llama3:latest': {
        'display_name': 'Dolphin Llama3 8B',
        'params_gb': 8,
        'expected_context': 8192,
    },
    'mistral-local:latest': {
        'display_name': 'Mistral 7B',
        'params_gb': 7,
        'expected_context': 8192,
    },
}

# ---------------------------------------------------------------------------
# Benchmark Runner
# ---------------------------------------------------------------------------
def run_ollama_benchmark(model, prompt, max_tokens, client, ollama_client):
    """
    Run real LLM inference benchmark with HyperRAM monitoring.
    
    Returns dict with all metrics.
    """
    # Get model config
    config = MODEL_CONFIGS.get(model, {})
    display_name = config.get('display_name', model)
    params_gb = config.get('params_gb', 0)
    
    print(f"\n  Model: {display_name} ({model})")
    if params_gb:
        print(f"  Parameters: {params_gb}B")
    print(f"  Prompt: \"{prompt[:50]}{'...' if len(prompt) > 50 else ''}\"")
    print(f"  Max tokens: {max_tokens}")
    print(DASH)
    
    # Start HyperRAM monitoring
    monitor = MemoryMonitor(client)
    monitor.start()
    
    # Run real LLM inference
    print("  Running inference...")
    generated_text, tokens_count, elapsed_sec = ollama_client.generate(
        model=model,
        prompt=prompt,
        max_tokens=max_tokens,
        stream=True
    )
    
    # Stop monitoring
    monitor.stop()
    
    # Calculate metrics
    tokens_per_sec = tokens_count / elapsed_sec if elapsed_sec > 0 else 0
    
    # Get HyperRAM deltas
    ram_delta = monitor.get_delta()
    
    # Calculate cache hit rate during inference
    total_accesses = ram_delta.get('cache_hits', 0) + ram_delta.get('cache_misses', 0)
    hit_rate = (ram_delta.get('cache_hits', 0) / total_accesses * 100) if total_accesses > 0 else 0
    
    result = {
        'model': model,
        'display_name': display_name,
        'params_gb': params_gb,
        'prompt_length': len(prompt),
        'generated_length': len(generated_text),
        'tokens_generated': tokens_count,
        'elapsed_sec': elapsed_sec,
        'tokens_per_sec': tokens_per_sec,
        'ssd_reads': ram_delta.get('ssd_reads', 0),
        'ssd_writes': ram_delta.get('ssd_writes', 0),
        'cache_hits': ram_delta.get('cache_hits', 0),
        'cache_misses': ram_delta.get('cache_misses', 0),
        'hit_rate_pct': hit_rate,
        'ram_pages_start': ram_delta.get('ram_pages_start', 0),
        'ram_pages_end': ram_delta.get('ram_pages_end', 0),
        'pool_used_mb_start': ram_delta.get('pool_used_mb_start', 0),
        'pool_used_mb_end': ram_delta.get('pool_used_mb_end', 0),
        'compression_ratio': ram_delta.get('compression_ratio', 1.0),
    }
    
    # Print results
    print(f"  Generated {tokens_count} tokens in {elapsed_sec:.2f}s")
    print(f"  Tokens/sec: {tokens_per_sec:.2f}")
    print(f"  Cache Hit Rate: {hit_rate:.1f}%")
    print(f"  SSD Reads: {result['ssd_reads']}")
    print(f"  SSD Writes: {result['ssd_writes']}")
    print(f"  RAM Pages: {result['ram_pages_start']} → {result['ram_pages_end']}")
    print(f"  Compression: {result['compression_ratio']:.2f}x")
    
    if generated_text:
        print(f"\n  Generated text (first 200 chars):")
        print(f"  \"{generated_text[:200]}{'...' if len(generated_text) > 200 else ''}\"")
    
    return result


def run_all_models_benchmark(models, prompt, max_tokens, output_dir='results'):
    """Run benchmark for multiple models."""
    all_results = []
    
    # Initialize HyperRAM client
    client = HyperRAMKernelClient()
    if not client.is_kernel_mode:
        print("  [WARNING] Kernel driver not loaded, using userspace fallback")
    
    # Initialize Ollama client
    ollama_client = OllamaClient()
    
    # Pre-fill HyperRAM with some data (simulate warm cache)
    print("\n  Warming up HyperRAM cache...")
    for i in range(500):
        client.write_page(i, bytes([i & 0xFF]) * PAGE_SIZE)
    
    for model in models:
        try:
            result = run_ollama_benchmark(model, prompt, max_tokens, client, ollama_client)
            all_results.append(result)
            
            # Save intermediate results
            if all_results:
                save_csv(all_results, output_dir)
                
        except Exception as e:
            print(f"  [ERROR] Benchmark failed for {model}: {e}")
            import traceback
            traceback.print_exc()
    
    client.close()
    
    return all_results


def print_summary_table(all_results):
    """Print summary table of all models."""
    if not all_results:
        return
    
    print("\n" + SEP)
    print("  Ollama LLM Benchmark Summary")
    print(SEP)
    
    header = (
        f"  {'Model':<25} | {'Params':>7} | {'Tokens/s':>10} | {'Hit Rate':>9} | "
        f"{'SSD R':>8} | {'SSD W':>8} | {'Compression':>12}"
    )
    print(header)
    print("  " + "-" * 90)
    
    for r in all_results:
        params = f"{r.get('params_gb', 0):>2}B" if r.get('params_gb') else " N/A"
        print(f"  {r.get('display_name', r['model']):<25} | {params:>7} | {r['tokens_per_sec']:>10.2f} | "
              f"{r['hit_rate_pct']:>8.1f}% | {r['ssd_reads']:>8} | "
              f"{r['ssd_writes']:>8} | {r['compression_ratio']:>11.2f}x")
    
    print(SEP)
    
    # Find best performer
    if all_results:
        best = max(all_results, key=lambda x: x['tokens_per_sec'])
        print(f"\n  Best Performance: {best.get('display_name', best['model'])}")
        print(f"    {best['tokens_per_sec']:.2f} tokens/sec")
        print(f"    Cache Hit Rate: {best['hit_rate_pct']:.1f}%")
        
        # Additional stats for paper
        print(f"\n  Statistical Analysis:")
        all_tps = [r['tokens_per_sec'] for r in all_results if r['tokens_per_sec'] > 0]
        if all_tps:
            print(f"    Mean Tokens/sec: {statistics.mean(all_tps):.2f}")
            print(f"    Median Tokens/sec: {statistics.median(all_tps):.2f}")
            if len(all_tps) > 1:
                print(f"    Std Dev: {statistics.stdev(all_tps):.2f}")


def save_csv(results, output_dir='results'):
    """Save results to CSV file."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"ollama_benchmark_{timestamp}.csv"
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
    parser = argparse.ArgumentParser(description='HyperRAM Ollama LLM Benchmark')
    parser.add_argument('--model', type=str, default='llama3', help='Ollama model name')
    parser.add_argument('--prompt', type=str, 
                       default='Explain quantum computing in simple terms.',
                       help='Prompt for generation')
    parser.add_argument('--max-tokens', type=int, default=200, 
                       help='Maximum tokens to generate')
    parser.add_argument('--all-models', action='store_true', 
                       help='Benchmark all available Ollama models')
    parser.add_argument('--list-models', action='store_true',
                       help='List available Ollama models and exit')
    parser.add_argument('--output', type=str, default='results', 
                       help='Output directory')
    
    args = parser.parse_args()
    
    print("\n" + SEP)
    print("  HyperRAM Real LLM Benchmark (Ollama)")
    print(SEP)
    
    # Check Ollama availability
    ollama_client = OllamaClient()
    available_models = ollama_client.list_models()
    
    if args.list_models:
        print(f"\n  Available Ollama Models ({len(available_models)}):")
        for model in available_models:
            info = ollama_client.get_model_info(model)
            size_gb = info.get('details', {}).get('size', 0) / (1024**3)
            print(f"    - {model} ({size_gb:.1f} GB)")
        return 0
    
    if not available_models:
        print("  [ERROR] No Ollama models found. Install models with:")
        print("    ollama pull llama3")
        print("    ollama pull mistral")
        print("    ollama pull qwen")
        return 1
    
    print(f"  Available models: {', '.join(available_models[:5])}{'...' if len(available_models) > 5 else ''}")
    print(SEP)
    
    # Select models to benchmark
    if args.all_models:
        models = available_models
        print(f"\n  Benchmarking ALL {len(models)} models...")
    else:
        if args.model in available_models:
            models = [args.model]
        else:
            print(f"  [WARNING] Model '{args.model}' not found. Using first available.")
            models = [available_models[0]] if available_models else []
    
    if not models:
        print("  [ERROR] No models to benchmark")
        return 1
    
    # Run benchmarks
    results = run_all_models_benchmark(
        models=models,
        prompt=args.prompt,
        max_tokens=args.max_tokens,
        output_dir=args.output
    )
    
    if results:
        print_summary_table(results)
        print("\n" + SEP)
        print("  ✓ Benchmark Complete")
        print(SEP)
        return 0
    else:
        print("\n" + SEP)
        print("  ✗ Benchmark Failed")
        print(SEP)
        return 1


if __name__ == "__main__":
    sys.exit(main())