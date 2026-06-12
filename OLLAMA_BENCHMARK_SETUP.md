# Ollama LLM Benchmark Setup Guide

## Prerequisites

### 1. Install Ollama

**Windows:**
```bash
# Download from https://ollama.ai/download
# Or install via winget
winget install Ollama.Ollama
```

**Verify Installation:**
```bash
ollama --version
ollama serve
```

### 2. Pull LLM Models

```bash
# Recommended models for benchmarking
ollama pull llama3.2          # 3B - Fast, good for testing
ollama pull llama3.1          # 8B - Balanced performance
ollama pull mistral           # 7B - Popular general model
ollama pull mistral-nemo      # 12B - Larger context
ollama pull qwen2.5           # 7B-14B - Good coding model
ollama pull deepseek-coder    # 6.7B - Code generation
ollama pull phi3              # 3.8B - Microsoft's efficient model
ollama pull gemma2            # 9B - Google's model
ollama pull mixtral           # 8x7B MoE - High quality

# Check available models
ollama list
```

### 3. Verify Ollama is Running

```bash
# Should return list of models
curl http://localhost:11434/api/tags

# Or use the benchmark script
cd hyperram-daemon
python ai_benchmark_ollama.py --list-models
```

---

## Running Benchmarks

### Quick Test (Single Model)
```bash
cd hyperram-daemon
python ai_benchmark_ollama.py --model llama3.2 --max-tokens 50
```

### Full Benchmark (All Models)
```bash
python ai_benchmark_ollama.py --all-models --max-tokens 300
```

### Custom Prompt
```bash
python ai_benchmark_ollama.py \
  --model mistral \
  --prompt "Explain how neural networks learn in 3 paragraphs" \
  --max-tokens 200
```

### With HyperRAM Kernel Driver
```bash
# Make sure driver is loaded
sc query HyperRAM

# Run benchmark (will monitor HyperRAM stats)
python ai_benchmark_ollama.py --all-models --max-tokens 300
```

---

## Integration with Full Benchmark Suite

### Run Complete Suite with Ollama
```bash
python run_all_benchmarks.py --ollama
```

### Quick Validation with Ollama
```bash
python run_all_benchmarks.py --quick --ollama
```

### AI Benchmarks Only
```bash
python run_all_benchmarks.py --ai-only --ollama
```

---

## Output Files

Benchmark results are saved to:
```
results/
├── ollama_benchmark_YYYYMMDD_HHMMSS.csv
└── paper_YYYYMMDD_HHMMSS/
    ├── benchmark_summary.json
    └── benchmark_report.txt
```

### CSV Columns

| Column | Description |
|--------|-------------|
| `model` | Ollama model name |
| `tokens_generated` | Actual tokens generated |
| `elapsed_sec` | Total generation time |
| `tokens_per_sec` | Inference speed |
| `hit_rate_pct` | HyperRAM cache hit rate |
| `ssd_reads` | NVMe reads during inference |
| `ssd_writes` | NVMe writes during inference |
| `compression_ratio` | Data compression ratio |
| `ram_pages_start` | RAM pages at start |
| `ram_pages_end` | RAM pages at end |

---

## Expected Performance

### Tokens/sec by Model (Approximate)

| Model | Size | CPU-only (tok/s) | With GPU (tok/s) |
|-------|------|------------------|------------------|
| llama3.2 | 3B | 20-30 | 80-120 |
| llama3.1 | 8B | 10-15 | 50-80 |
| mistral | 7B | 12-18 | 60-90 |
| qwen2.5 | 7B | 12-18 | 60-90 |
| deepseek-coder | 6.7B | 13-19 | 65-95 |
| phi3 | 3.8B | 18-25 | 75-110 |
| mixtral | 8x7B | 5-8 | 30-50 |

*Note: Performance varies by CPU/GPU. HyperRAM improves effective throughput by reducing memory pressure.*

---

## Troubleshooting

### Ollama Not Running
```bash
# Start Ollama service
ollama serve

# Or run as background process
Start-Process ollama serve
```

### Model Not Found
```bash
# Pull the model
ollama pull <model-name>

# Check available models
ollama list
```

### HyperRAM Driver Not Loaded
```bash
# Check status
sc query HyperRAM

# Start driver
sc start HyperRAM

# If not installed
sc create HyperRAM type= kernel binPath= C:\path\to\HyperRAM.sys
sc start HyperRAM
```

### Connection Refused
```bash
# Check Ollama is listening
netstat -an | findstr 11434

# Should show: 0.0.0.0:11434 LISTENING
```

---

## Paper Integration

### Updated Paper Section (Section 4.2)

Replace simulated AI benchmark table with real Ollama results:

**Table 1: Real LLM Inference Metrics via Ollama (300 tokens)**

| Model | Size | Tokens/sec | Cache Hit% | SSD Reads | Compression |
|-------|------|------------|------------|-----------|-------------|
| llama3.2 | 3B | [TBD] | [TBD]% | [TBD] | [TBD]x |
| llama3.1 | 8B | [TBD] | [TBD]% | [TBD] | [TBD]x |
| mistral | 7B | [TBD] | [TBD]% | [TBD] | [TBD]x |
| qwen2.5 | 7B | [TBD] | [TBD]% | [TBD] | [TBD]x |
| deepseek-coder | 6.7B | [TBD] | [TBD]% | [TBD] | [TBD]x |
| phi3 | 3.8B | [TBD] | [TBD]% | [TBD] | [TBD]x |

*Run: `python ai_benchmark_ollama.py --all-models --max-tokens 300`*

---

## Advanced Usage

### Monitor Specific Model Performance
```python
from ai_benchmark_ollama import OllamaClient, MemoryMonitor
from kernel_client import HyperRAMKernelClient

# Initialize
ollama = OllamaClient()
ram_client = HyperRAMKernelClient()
monitor = MemoryMonitor(ram_client)

# Start monitoring
monitor.start()

# Generate text
text, tokens, elapsed = ollama.generate(
    model='llama3.1',
    prompt='Write a haiku about quantum physics',
    max_tokens=100
)

# Stop monitoring
monitor.stop()

# Get stats
delta = monitor.get_delta()
print(f"Tokens/sec: {tokens/elapsed:.2f}")
print(f"Cache hit rate: {delta['hit_rate_pct']:.1f}%")
```

### Batch Benchmark Script
```bash
#!/bin/bash
# benchmark_all_models.sh

models=("llama3.2" "llama3.1" "mistral" "qwen2.5" "phi3")

for model in "${models[@]}"; do
    echo "Benchmarking $model..."
    python ai_benchmark_ollama.py --model $model --max-tokens 300
    sleep 5  # Cool-down between runs
done
```

---

## Notes for Paper Authors

1. **Real vs Simulated**: Ollama benchmarks measure REAL inference performance, not simulated access patterns. This strengthens the paper's AI workload claims.

2. **HyperRAM Impact**: The benchmark monitors HyperRAM stats DURING inference, showing actual memory access patterns from LLM execution.

3. **Reproducibility**: All Ollama models are publicly available. Include exact model versions in paper (e.g., "llama3.1:8b-instruct-q4_K_M").

4. **Hardware Notes**: Document CPU/GPU configuration as Ollama performance varies significantly with hardware.

5. **Comparison**: Can compare Ollama results with simulated `ai_benchmark.py` to validate simulation accuracy.