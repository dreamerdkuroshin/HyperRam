# HyperRAM Benchmark Time Estimates

## Quick Reference

| Mode | Time Required | Use Case |
|------|--------------|----------|
| **Quick Validation** | 15-20 minutes | Pre-submission check |
| **Full Suite** | 35-70 minutes | Paper submission |
| **Full + Stability** | 1.5-25 hours | Complete validation |

---

## Detailed Breakdown

### 1. Security & Stress Tests - 2-3 minutes

```bash
python security_stress_test.py
```

**Tests:**
- IOCTL buffer validation: 30 seconds
- Race condition testing (1, 4, 8, 16, 64 threads): 1 minute
- Fuzzing (invalid IDs, random IOCTLs): 30 seconds
- Stability test (60s): 1 minute

**Output:** `security_stress_test_stdout.log`

---

### 2. AI Benchmark (Ollama) - 15-30 minutes

```bash
python ai_benchmark_ollama.py --all-models --max-tokens 200
```

**Time per model:**
- Small models (4-8B): 1-2 minutes each
- Medium models (14-30B): 3-5 minutes each
- Large models (70B+): 5-10 minutes each

**Your models (13 total):**
1. deepseek-r1:8b - ~2 min
2. beru-unbound:latest - ~2 min
3. Beru-Unrestricted:latest - ~2 min
4. Beru-1.0-MYT:latest - ~2 min
5. dolphin-llama3:latest - ~2 min
6. llama3:latest - ~2 min
7. phi3-local:latest - ~1 min (smaller)
8. llama3-local:latest - ~1 min (smaller)
9. qwen-local:latest - ~3 min
10. mistral-local:latest - ~2 min
11. dolphin-local:latest - ~2 min
12. nemomix-local:latest - ~5 min (larger)
13. gemma3:4b - ~1 min

**Total: ~27 minutes** for all 13 models at 200 tokens each

**Quick mode (50 tokens):** ~7 minutes

**Output:** `ollama_benchmark_YYYYMMDD_HHMMSS.csv`

---

### 3. Multi-thread Scalability - 5-10 minutes

```bash
python multithread_benchmark.py --threads 1,4,8,16,64
```

**Time per thread count:**
- 1 thread: 30 seconds
- 4 threads: 1 minute
- 8 threads: 2 minutes
- 16 threads: 3 minutes
- 64 threads: 5 minutes

**Total: ~11 minutes**

**Quick mode (1,4,8 only):** ~4 minutes

**Output:** `multithread_benchmark_YYYYMMDD_HHMMSS.csv`

---

### 4. Research Benchmark (R1-R12) - 10-20 minutes

```bash
python research_benchmark.py
```

**Sections:**
- R1: Hit-rate sensitivity - 2 min
- R2: Sequential vs random - 2 min
- R3: Graph workload - 2 min
- R4: AI inference - 2 min
- R5: Compilation workload - 1 min
- R6: Database workload - 2 min
- R7: CPU overhead - 1 min
- R8: Write amplification - 2 min
- R9: Related work - 1 min (comparison table)
- R10: Tail latency - 2 min
- R11: Crash recovery - 1 min
- R12: Memory pressure - 2 min

**Total: ~20 minutes**

**Quick mode:** ~5 minutes (reduced iterations)

**Output:** `research_benchmark_YYYYMMDD_HHMMSS.csv`

---

### 5. Power Benchmark - 3-5 minutes

```bash
python power_benchmark.py --pages 1000 --reads 5000
```

**Tests:**
- Baseline power measurement: 1 min
- Active I/O power: 2 min
- Idle power: 1 min
- Analysis: 1 min

**Total: ~5 minutes**

**Output:** `power_benchmark_YYYYMMDD_HHMMSS.csv`

---

### 6. Stability Test (Optional) - 1-24 hours

```bash
python stability_test.py --duration 24h
```

**Options:**
- Quick stability: 1 hour
- Standard: 6 hours
- Extended: 24 hours

**Monitors:**
- Memory leaks
- Page corruption
- Counter overflow
- Deadlock detection

**Output:** `stability_test_YYYYMMDD_HHMMSS.csv`

---

### 7. Paper Generation - 1-2 minutes

```bash
python generate_paper_results.py
```

**Generates:**
- Executive summary
- Security results (4 sections)
- AI benchmark tables
- Scalability analysis
- Research questions (R1-R12)
- Performance comparisons
- Conclusion

**Output:** `paper_results_YYYYMMDD_HHMMSS.md` (30-40 pages)

---

## Complete Suite Timing

### Quick Validation Mode
```bash
python run_complete_benchmarks.py --quick
```

| Benchmark | Time |
|-----------|------|
| Security | 2 min |
| AI (50 tokens) | 7 min |
| Multithread (1,4,8) | 4 min |
| Research (quick) | 5 min |
| Power | 5 min |
| Paper generation | 2 min |
| **TOTAL** | **~25 minutes** |

---

### Full Paper Mode (Recommended)
```bash
python run_complete_benchmarks.py
```

| Benchmark | Time |
|-----------|------|
| Security | 3 min |
| AI (200 tokens, all models) | 27 min |
| Multithread (1,4,8,16,64) | 11 min |
| Research (full) | 20 min |
| Power | 5 min |
| Paper generation | 2 min |
| **TOTAL** | **~68 minutes** (1 hour 8 min) |

---

### Complete Validation Mode
```bash
python run_complete_benchmarks.py --stability
```

| Benchmark | Time |
|-----------|------|
| Full suite (above) | 68 min |
| Stability test (1h) | 60 min |
| **TOTAL** | **~128 minutes** (2 hours 8 min) |

For 24-hour stability: **~25 hours total**

---

## Recommended Workflow

### Day 1: Quick Validation (25 min)
```bash
python run_complete_benchmarks.py --quick
```
- Verify all benchmarks run
- Check for crashes/errors
- Review quick results

### Day 2: Full Paper Run (1h 10min)
```bash
python run_complete_benchmarks.py
```
- Run complete suite
- Generate 30-40 page paper
- Review results

### Day 3-4: Extended Stability (Optional)
```bash
python stability_test.py --duration 24h
```
- Run overnight
- Validate zero crashes
- Check for memory leaks

---

## Output Files

All results saved to: `results/paper_YYYYMMDD_HHMMSS/`

```
paper_20260612_143022/
├── security_stress_test_stdout.log
├── security_stress_test_stderr.log
├── ollama_benchmark_stdout.log
├── ollama_benchmark_stderr.log
├── multithread_benchmark_stdout.log
├── multithread_benchmark_stderr.log
├── research_benchmark_stdout.log
├── research_benchmark_stderr.log
├── power_benchmark_stdout.log
├── power_benchmark_stderr.log
├── ollama_benchmark_20260612_143022.csv
├── multithread_benchmark_20260612_143022.csv
├── research_benchmark_20260612_143022.csv
├── power_benchmark_20260612_143022.csv
└── paper_results_20260612_143022.md (30-40 pages)
```

---

## Performance Tips

### Faster AI Benchmarks
1. Reduce token count: `--max-tokens 50` (default: 200)
2. Benchmark subset: `--model llama3 --model mistral` (instead of `--all-models`)
3. Use smaller models first for validation

### Faster Multithread
1. Reduce pages: `--pages 500` (default: 1000)
2. Reduce reads: `--reads-per-thread 200` (default: 500)
3. Fewer thread configs: `--threads 1,4,16` (skip 8, 64)

### Faster Research
1. Quick mode: Built-in quick validation
2. Skip sections: Comment out non-critical R# sections
3. Reduce iterations in `research_benchmark.py`

---

## Troubleshooting

### Benchmark Takes Too Long
- **Cause:** Too many models or iterations
- **Fix:** Use `--quick` flag or reduce parameters

### Ollama Connection Failed
- **Cause:** Ollama not running
- **Fix:** `ollama serve` in another terminal

### Out of Memory
- **Cause:** Large models + many threads
- **Fix:** Close other apps, reduce thread count

### Driver Not Found
- **Cause:** Kernel driver not loaded
- **Fix:** Benchmarks fall back to userspace mode automatically

---

## Next Steps After Running

1. **Review paper results:** Open `paper_results_*.md`
2. **Generate graphs:** Use CSV files in plotting tool
3. **Write paper sections:** Use results as data source
4. **Submit:** Ready for conference submission

---

**Last Updated:** 2026-06-12  
**Estimated Total Time:** 15 min (quick) to 25 hours (full + 24h stability)