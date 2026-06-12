from collections import OrderedDict
import random

class MockSystem:
    def __init__(self, prefetch_mode="fixed", max_cache=256):
        self.prefetch_mode = prefetch_mode
        self.max_cache = max_cache
        self.ram_cache = OrderedDict() # page_id -> True (LRU cache)
        self.page_table = set() # Simulated SSD pages
        self.cache_hits = 0
        self.cache_misses = 0
        self.prefetches_triggered = 0
        self.prefetches_wasted = 0
        
        # Tau predictor state
        self.last_access_time = None
        self.inter_arrival_tau = 0.010 # 10ms default
        self.tau_variance = 0.0
        self.last_page_id = 0
        self.last_stride = 1
        self.stride_confidence = 0

    def access(self, page_id, current_time):
        self.page_table.add(page_id)
        
        # Check cache
        if page_id in self.ram_cache:
            self.cache_hits += 1
            hit = True
            # Move to end (most recently used)
            self.ram_cache.move_to_end(page_id)
        else:
            self.cache_misses += 1
            hit = False
            
            # Evict LRU if full
            if len(self.ram_cache) >= self.max_cache:
                self.ram_cache.popitem(last=False)
            self.ram_cache[page_id] = True
            
        # Timing and Variance calculation
        if self.last_access_time is not None:
            delta_t = current_time - self.last_access_time
            # FIX Bug-5: capture old tau BEFORE updating, then compute variance
            # against old_tau so |delta - old_tau| measures true deviation.
            old_tau = self.inter_arrival_tau
            self.inter_arrival_tau = 0.85 * old_tau + 0.15 * delta_t
            self.tau_variance = 0.85 * self.tau_variance + 0.15 * abs(delta_t - old_tau)
        self.last_access_time = current_time

        # Stride calculation
        current_stride = page_id - self.last_page_id
        if current_stride == self.last_stride:
            self.stride_confidence = min(8, self.stride_confidence + 1)
        else:
            self.stride_confidence = max(0, self.stride_confidence - 2)
            self.last_stride = current_stride
        self.last_page_id = page_id

        # Prefetching
        prefetched_now = []
        if self.prefetch_mode == "fixed":
            # Fixed N+4 lookahead (original prefetcher)
            for lookahead in [1, 2, 3, 4]:
                next_p = page_id + lookahead
                if next_p not in self.ram_cache:
                    if len(self.ram_cache) >= self.max_cache:
                        self.ram_cache.popitem(last=False)
                    self.ram_cache[next_p] = True
                    self.prefetches_triggered += 1
                    prefetched_now.append(next_p)
        elif self.prefetch_mode == "tau":
            # Adaptive Tau Predictor with Fast Unpredictability Detection
            prefetch_depth = 0
            is_stable = self.tau_variance <= (0.5 * self.inter_arrival_tau)
            if self.stride_confidence >= 3 and self.last_stride != 0 and is_stable:
                # Scale prefetch depth inversely with tau (seconds)
                prefetch_depth = min(8, max(1, int(0.012 / (self.inter_arrival_tau + 1e-6))))
            
            for d in range(1, prefetch_depth + 1):
                next_p = page_id + d * self.last_stride
                if next_p not in self.ram_cache:
                    if len(self.ram_cache) >= self.max_cache:
                        self.ram_cache.popitem(last=False)
                    self.ram_cache[next_p] = True
                    self.prefetches_triggered += 1
                    prefetched_now.append(next_p)
                    
        return hit

def generate_workload(pattern, size):
    random.seed(42)
    pages = []
    times = []
    curr_time = 0.0
    
    if pattern == "sequential":
        for i in range(size):
            pages.append(i)
            times.append(curr_time)
            curr_time += 0.002 # 2ms steady
            
    elif pattern == "strided":
        # +4 stride: 1, 5, 9, 13...
        for i in range(size):
            pages.append(i * 4)
            times.append(curr_time)
            curr_time += 0.002 # 2ms steady
            
    elif pattern == "mixed":
        # 1,2,3, 100,101,102...
        curr_page = 0
        for i in range(size):
            if i % 10 == 0 and i > 0:
                curr_page += 100 # sudden jump
            else:
                curr_page += 1
            pages.append(curr_page)
            times.append(curr_time)
            curr_time += 0.002 if (i % 10 != 0) else 0.050 # jump has delay
            
    elif pattern == "random":
        for _ in range(size):
            pages.append(random.randint(0, 10000))
            times.append(curr_time)
            curr_time += random.uniform(0.001, 0.050)
            
    return pages, times

def run_experiment(pattern, size, mode):
    pages, times = generate_workload(pattern, size)
    sys = MockSystem(prefetch_mode=mode)
    
    hits = 0
    for p, t in zip(pages, times):
        if sys.access(p, t):
            hits += 1
            
    hit_rate = (hits / size) * 100 if size > 0 else 0
    return hit_rate, sys.prefetches_triggered

def main():
    workloads = ["sequential", "strided", "mixed", "random"]
    sizes = [50, 100, 1000, 10000]
    
    print(f"{'Workload':<12} | {'Size':<6} | {'Fixed N+4 Hit%':<15} | {'Fixed Prefetches':<16} | {'Tau Predictor Hit%':<18} | {'Tau Prefetches':<14}")
    print("-" * 92)
    
    for wl in workloads:
        for sz in sizes:
            fixed_hit, fixed_pref = run_experiment(wl, sz, "fixed")
            tau_hit, tau_pref = run_experiment(wl, sz, "tau")
            print(f"{wl:<12} | {sz:<6} | {fixed_hit:>13.1f}% | {fixed_pref:>16d} | {tau_hit:>16.1f}% | {tau_pref:>14d}")

if __name__ == "__main__":
    main()
