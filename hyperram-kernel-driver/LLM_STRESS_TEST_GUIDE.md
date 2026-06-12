# HyperRAM LLM Stress Testing Guide

## Overview

This guide explains how to run progressive LLM stress tests to validate HyperRAM under real-world AI workloads. The testing progression ensures stability before scaling to larger models.

## Stage Progression

**IMPORTANT:** Do not skip stages. Each stage validates HyperRAM under increasing memory pressure.

| Stage | Model Size | RAM Required | Min Duration | Target Tokens | Status |
|-------|-----------|--------------|--------------|---------------|--------|
| 1 | 4B (Gemma 3) | 2-3 GB | 5 min | 1,000 | Start here |
| 2 | 7B (Llama 3) | 4-5 GB | 10 min | 2,000 | After Stage 1 |
| 3 | 14B (Qwen 2.5) | 8-10 GB | 15 min | 3,000 | After Stage 2 |
| 4 | 32B (Qwen Coder) | 18-25 GB | 20 min | 4,000 | After Stage 3 |
| 5 | 70B (Llama 3.1) | 40-50 GB | 30 min | 5,000 | After Stage 4 |
| 6 | 120B+ (Mixtral) | 70-100+ GB | 60 min | 10,000 | Final stage |

## Prerequisites

### 1. Install Ollama

```bash
# Windows: Download from https://ollama.ai
# Or use winget
winget install Ollama.Ollama
```

### 2. Pull Models

```bash
# Stage 1
ollama pull gemma3:4b

# Stage 2
ollama pull llama3:8b

# Stage 3
ollama pull qwen2.5:14b

# Stage 4
ollama pull qwen2.5-coder:32b

# Stage 5
ollama pull llama3.1:70b

# Stage 6
ollama pull mixtral:8x22b
```

### 3. Start Ollama Server

```bash
# Terminal 1: Start Ollama
ollama serve
```

Keep this terminal open to monitor Ollama logs.

## Running Stress Tests

### Quick Start (Stage 1 Only)

```bash
cd hyperram-daemon

# Quick validation (5 minutes)
python llm_stress_benchmark.py --stage 1 --model gemma3:4b
```

### Full Progression (All Stages)

```bash
# Run all stages sequentially (2+ hours)
python llm_stress_benchmark.py --all-stages
```

### Individual Stage

```bash
# Stage 2 (7B model)
python llm_stress_benchmark.py --stage 2

# Stage 4 (32B model) with custom duration
python llm_stress_benchmark.py --stage 4 --duration 30m

# Stage 6 (120B model) with custom tokens
python llm_stress_benchmark.py --stage 6 --target-tokens 15000
```

## Metrics Monitored

### Primary Metrics

- **Model Load Success**: Did the model load without errors?
- **Tokens/sec**: Sustained inference throughput
- **Cache Hit Rate**: % of pages served from RAM cache
- **SSD Reads/Writes**: Physical NVMe I/O operations
- **Evictions**: Pages removed from cache under pressure
- **Compression Ratio**: XPress Huffman effectiveness

### Integrity Metrics

- **Data Integrity**: Hash verification of read/write operations
- **Error Count**: Number of failed operations
- **Runtime Duration**: Time before failure (if any)
- **Ollama Health**: Server responsiveness during test

## Interpreting Results

### Passing Criteria

**Stage is considered PASSED if:**

- ✓ Model loads successfully
- ✓ ≥100 tokens generated
- ✓ Cache hit rate > 50%
- ✓ Zero data corruptions detected
- ✓ Ollama server remains responsive
- ✓ No BSODs or system crashes

### Failure Modes

| Symptom | Likely Cause | Next Step |
|---------|--------------|-----------|
| Ollama exits immediately | Out of memory | Reduce model size, check RAM |
| Tokens/sec < 1 | Excessive SSD latency | Increase RAM cache size |
| Cache hit rate < 20% | Working set too large | Reduce context or batch size |
| Data corruption detected | Race condition in eviction | Check Driver.cpp spin-locks |
| Ollama timeout | Page access too slow | Check compression overhead |
| BSOD during test | Kernel driver bug | Check IRQL, page faults |

## Debugging Ollama Issues

### 1. Monitor Ollama Logs

In the terminal running `ollama serve`, watch for:

```
# Normal operation
2026-06-12 10:30:00 INFO model loaded successfully
2026-06-12 10:30:01 INFO generating tokens...

# Error: Out of memory
2026-06-12 10:30:00 ERROR failed to load model: out of memory

# Error: Corrupted data
2026-06-12 10:30:00 ERROR GGUF validation failed: checksum mismatch

# Error: Access violation
2026-06-12 10:30:00 ERROR segmentation fault in worker thread
```

### 2. Check HyperRAM Stats

```bash
# During inference, run in another terminal
python kernel_client.py --stats
```

Look for:
- High eviction rate → Cache too small
- High SSD reads → Low cache hit rate
- Compression ratio < 1.0 → Compression bug

### 3. Run Data Integrity Test

If Stage 1 fails, run:

```bash
# Comprehensive integrity test
python data_integrity_test.py --test all --pages 10000 --threads 16
```

This isolates whether the issue is:
- **Compression bug**: Pattern stress test fails
- **Race condition**: Concurrent access test fails
- **Eviction bug**: Eviction under load test fails

## Advanced Testing

### Custom Prompts

```bash
# Test with specific prompt type
python llm_stress_benchmark.py --stage 1 \
  --prompt "Write a Python function to implement quicksort"
```

### Extended Duration

```bash
# 1-hour stability test with 4B model
python llm_stress_benchmark.py --stage 1 --duration 1h
```

### Multi-Model Concurrent Test

```bash
# Run multiple models simultaneously (advanced)
python -c "
import subprocess
import threading

def run_stage(stage, model):
    subprocess.run(['python', 'llm_stress_benchmark.py', 
                   '--stage', str(stage), '--model', model])

# Concurrent Stage 1 and 2
t1 = threading.Thread(target=run_stage, args=(1, 'gemma3:4b'))
t2 = threading.Thread(target=run_stage, args=(2, 'llama3:8b'))
t1.start(); t2.start()
t1.join(); t2.join()
"
```

## Expected Results

### Stage 1 (4B Model)

```
  Stage Summary:
    Duration: 300.0s (5.0 min)
    Tokens Generated: 1,250
    Tokens/sec: 4.17
    Successful Runs: 10/10
    Failed Runs: 0
    Cache Hit Rate: 78.5%
    SSD Reads: 2,450
    SSD Writes: 1,230
    Compression: 2.15x
    Data Integrity: verified

  ✓ ALL INTEGRITY TESTS PASSED
```

### Stage 6 (120B+ Model)

```
  Stage Summary:
    Duration: 3600.0s (60.0 min)
    Tokens Generated: 12,500
    Tokens/sec: 3.47
    Successful Runs: 10/10
    Failed Runs: 0
    Cache Hit Rate: 65.2%
    SSD Reads: 45,000
    SSD Writes: 22,500
    Compression: 2.85x
    Data Integrity: verified

  ✓ STAGE 6 PASSED - HyperRAM validated for 120B+ models
```

## Paper Results

After successful completion:

```bash
# Generate graphs and paper
python run_all_benchmarks.py --generate

# Output:
# - results/graphs/scalability_curve.png
# - results/graphs/ai_performance_comparison.png
# - results/paper_results_YYYYMMDD_HHMMSS.md (30-40 pages)
```

## Troubleshooting

### Problem: Ollama won't start

```bash
# Check if port 11434 is in use
netstat -ano | findstr :11434

# Kill existing Ollama process
taskkill /F /IM ollama.exe

# Restart
ollama serve
```

### Problem: Model download fails

```bash
# Check internet connection
ping ollama.ai

# Try alternative mirror
OLLAMA_HOST=mirror.ollama.ai ollama pull gemma3:4b

# Or download manually from HuggingFace
```

### Problem: HyperRAM driver not loaded

```bash
# Check driver status
sc query HyperRAM

# If not running, install
cd hyperram-kernel-driver
sc create HyperRAM type= kernel binPath= C:\path\to\HyperRAM.sys
sc start HyperRAM
```

### Problem: Out of memory during Stage 3+

```bash
# Close other applications
# Reduce Windows page file size temporarily
# Or increase physical RAM

# Alternative: Use smaller quantization
ollama pull qwen2.5:14b-q4_K_M  # 4-bit quantized version
```

## Next Steps After Validation

1. **Run complete benchmark suite:**
   ```bash
   python run_all_benchmarks.py
   ```

2. **Generate paper results:**
   ```bash
   python generate_paper_results.py
   ```

3. **Create visual graphs:**
   ```bash
   python plot_paper_graphs.py --all
   ```

4. **Review results:**
   - `results/paper_*/benchmark_report.txt`
   - `results/graphs/*.png`
   - `results/paper_results_*.md`

## Contact & Support

- GitHub Issues: https://github.com/your-repo/hyperram/issues
- Documentation: See `PAPER_DRAFT.md` for research context
- Benchmark Guide: See `BENCHMARK_TIME_ESTIMATES.md`

---

**Last Updated:** 2026-06-12  
**Version:** 1.0  
**Status:** Production-ready for Stage 1-6 validation