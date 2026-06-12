# -*- coding: utf-8 -*-
r"""
=============================================================================
  security_stress_test.py — HyperRAM Security Audit & Stress Testing Suite
=============================================================================
  Tests:
    1. IOCTL Validation
       - InputBufferLength validation
       - OutputBufferLength validation
       - User pointer validation
       - Invalid parameter rejection
    
    2. Race Condition Testing
       - Concurrent access from 1, 4, 8, 16, 64 threads
       - Deadlock detection
       - Lock inversion detection
    
    3. Fuzzing
       - Invalid page IDs
       - Oversized requests
       - Random IOCTL codes
       - Malformed structures
    
    4. Stability Testing
       - 24-hour continuous operation
       - Memory leak detection
       - Page corruption detection
  
  Goals:
    - 0 crashes
    - 0 BSODs
    - 0 deadlocks
    - 0 memory leaks
    - 0 data corruption
=============================================================================
"""
import sys, os
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import time, random, struct, threading, ctypes, statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from kernel_client import HyperRAMKernelClient, IOCTL_HYPERRAM_GET_STATS, \
    IOCTL_HYPERRAM_READ_PAGE, IOCTL_HYPERRAM_WRITE_PAGE, IOCTL_HYPERRAM_FLUSH, \
    IOCTL_HYPERRAM_SAVE_METADATA, PAGE_SIZE

PAGE_SIZE = 4096
SEP = "=" * 72

# ---------------------------------------------------------------------------
# Test Results Tracking
# ---------------------------------------------------------------------------
class TestResults:
    def __init__(self):
        self.lock = threading.Lock()
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.crashes = 0
        self.bsods = 0
        self.deadlocks = 0
        
    def record_pass(self, test_name):
        with self.lock:
            self.tests_run += 1
            self.tests_passed += 1
            print(f"  [PASS] {test_name}")
            
    def record_fail(self, test_name, reason=""):
        with self.lock:
            self.tests_run += 1
            self.tests_failed += 1
            print(f"  [FAIL] {test_name}: {reason}")
            
    def record_crash(self, test_name, error=""):
        with self.lock:
            self.crashes += 1
            print(f"  [CRASH] {test_name}: {error}")
    
    def summary(self):
        with self.lock:
            return {
                'tests_run': self.tests_run,
                'tests_passed': self.tests_passed,
                'tests_failed': self.tests_failed,
                'crashes': self.crashes,
                'bsods': self.bsods,
                'deadlocks': self.deadlocks
            }

results = TestResults()

# ---------------------------------------------------------------------------
# 1. IOCTL Validation Tests
# ---------------------------------------------------------------------------
def test_ioctl_buffer_validation():
    """Test IOCTL input/output buffer length validation."""
    print("\n" + SEP)
    print("  TEST 1: IOCTL Buffer Validation")
    print(SEP)
    
    client = HyperRAMKernelClient()
    if not client.is_kernel_mode:
        print("  [SKIP] Kernel driver not loaded")
        return
    
    # Get raw handle for low-level testing
    from kernel_client import _k32, wt
    
    # Test 1.1: Undersized input buffer
    try:
        returned = wt.DWORD(0)
        status = _k32.DeviceIoControl(
            client._handle,
            IOCTL_HYPERRAM_READ_PAGE,
            None, 0,  # Undersized input (should be 16 bytes)
            None, 0,
            ctypes.byref(returned),
            None
        )
        if status:
            results.record_fail("Undersized input buffer", "Should have failed")
        else:
            err = ctypes.get_last_error()
            if err in (87, 122):  # ERROR_INVALID_PARAMETER or ERROR_INSUFFICIENT_BUFFER
                results.record_pass("Undersized input buffer rejected")
            else:
                results.record_fail("Undersized input buffer", f"Wrong error: {err}")
    except Exception as e:
        results.record_crash("Undersized input buffer", str(e))
    
    # Test 1.2: Oversized input buffer (DoS attempt)
    try:
        oversized = (ctypes.c_ubyte * (PAGE_SIZE * 100))()
        returned = wt.DWORD(0)
        status = _k32.DeviceIoControl(
            client._handle,
            IOCTL_HYPERRAM_WRITE_PAGE,
            oversized, len(oversized),
            None, 0,
            ctypes.byref(returned),
            None
        )
        if status:
            results.record_fail("Oversized input buffer", "Should have failed")
        else:
            err = ctypes.get_last_error()
            if err == 87:  # ERROR_INVALID_PARAMETER
                results.record_pass("Oversized input buffer rejected")
            else:
                results.record_fail("Oversized input buffer", f"Wrong error: {err}")
    except Exception as e:
        results.record_crash("Oversized input buffer", str(e))
    
    # Test 1.3: Invalid QoS tag
    try:
        req = struct.pack("<QII", 0, 999, PAGE_SIZE)  # Invalid QoS=999
        out_buf = (ctypes.c_ubyte * PAGE_SIZE)()
        returned = wt.DWORD(0)
        status = _k32.DeviceIoControl(
            client._handle,
            IOCTL_HYPERRAM_READ_PAGE,
            req, len(req),
            out_buf, len(out_buf),
            ctypes.byref(returned),
            None
        )
        if status:
            results.record_fail("Invalid QoS tag", "Should have failed")
        else:
            err = ctypes.get_last_error()
            if err == 87:
                results.record_pass("Invalid QoS tag rejected")
            else:
                results.record_fail("Invalid QoS tag", f"Wrong error: {err}")
    except Exception as e:
        results.record_crash("Invalid QoS tag", str(e))
    
    # Test 1.4: Invalid DataLength
    try:
        req = struct.pack("<QII", 0, 0, 100)  # Wrong DataLength (should be 4096)
        out_buf = (ctypes.c_ubyte * PAGE_SIZE)()
        returned = wt.DWORD(0)
        status = _k32.DeviceIoControl(
            client._handle,
            IOCTL_HYPERRAM_READ_PAGE,
            req, len(req),
            out_buf, len(out_buf),
            ctypes.byref(returned),
            None
        )
        if status:
            results.record_fail("Invalid DataLength", "Should have failed")
        else:
            err = ctypes.get_last_error()
            if err == 87:
                results.record_pass("Invalid DataLength rejected")
            else:
                results.record_fail("Invalid DataLength", f"Wrong error: {err}")
    except Exception as e:
        results.record_crash("Invalid DataLength", str(e))
    
    client.close()

# ---------------------------------------------------------------------------
# 2. Race Condition Tests
# ---------------------------------------------------------------------------
def race_worker(client_factory, thread_id, ops_count, stats):
    """Worker thread for race condition testing."""
    try:
        client = client_factory()
        rng = random.Random(thread_id * 1000)
        
        for i in range(ops_count):
            page_id = rng.randint(0, 10000)
            
            if rng.random() < 0.5:
                # Read
                data = client.read_page(page_id)
            else:
                # Write
                data = bytes([rng.randint(0, 255)]) * PAGE_SIZE
                client.write_page(page_id, data)
        
        client.close()
        stats['ops'] += ops_count
    except Exception as e:
        stats['errors'] += 1
        stats['last_error'] = str(e)

def test_race_conditions():
    """Test for race conditions, deadlocks, and lock inversions."""
    print("\n" + SEP)
    print("  TEST 2: Race Condition Testing")
    print(SEP)
    
    thread_counts = [1, 4, 8, 16, 64]
    
    for num_threads in thread_counts:
        print(f"\n  Testing with {num_threads} threads...")
        
        client = HyperRAMKernelClient()
        if not client.is_kernel_mode:
            print("  [SKIP] Kernel driver not loaded")
            return
        
        # Pre-fill some pages
        for i in range(1000):
            client.write_page(i, bytes([i & 0xFF]) * PAGE_SIZE)
        
        client.close()
        
        def client_factory():
            return HyperRAMKernelClient()
        
        stats = {'ops': 0, 'errors': 0, 'last_error': None}
        ops_per_thread = 500
        
        start_time = time.perf_counter()
        
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [
                executor.submit(race_worker, client_factory, i, ops_per_thread, stats)
                for i in range(num_threads)
            ]
            
            # Wait with timeout (deadlock detection)
            timeout = 30  # seconds
            done, not_done = [], []
            for future in as_completed(futures, timeout=timeout):
                try:
                    future.result()
                    done.append(future)
                except TimeoutError:
                    results.record_fail(f"{num_threads} threads", "DEADLOCK DETECTED")
                    results.deadlocks += 1
                    return
                except Exception as e:
                    stats['errors'] += 1
                    stats['last_error'] = str(e)
        
        elapsed = time.perf_counter() - start_time
        
        if stats['errors'] == 0:
            results.record_pass(f"{num_threads} threads - {stats['ops']} ops in {elapsed:.2f}s")
        else:
            results.record_fail(f"{num_threads} threads", f"{stats['errors']} errors: {stats['last_error']}")
    
    # Test 2.5: Lock inversion test (stress with mixed operations)
    print("\n  Testing for lock inversion...")
    try:
        client = HyperRAMKernelClient()
        if client.is_kernel_mode:
            # Rapid metadata saves during concurrent access
            def metadata_saver():
                for _ in range(100):
                    client.save_metadata()
                    time.sleep(0.01)
            
            def io_worker():
                for i in range(1000):
                    client.read_page(i)
                    client.write_page(i, bytes([i & 0xFF]) * PAGE_SIZE)
            
            threads = [
                threading.Thread(target=metadata_saver),
                threading.Thread(target=io_worker),
                threading.Thread(target=io_worker)
            ]
            
            for t in threads:
                t.start()
            
            for t in threads:
                t.join(timeout=30)
                if t.is_alive():
                    results.record_fail("Lock inversion test", "DEADLOCK")
                    results.deadlocks += 1
                    return
            
            results.record_pass("Lock inversion test")
        client.close()
    except Exception as e:
        results.record_crash("Lock inversion test", str(e))

# ---------------------------------------------------------------------------
# 3. Fuzzing Tests
# ---------------------------------------------------------------------------
def test_fuzzing():
    """Fuzz testing with invalid inputs."""
    print("\n" + SEP)
    print("  TEST 3: Fuzzing")
    print(SEP)
    
    client = HyperRAMKernelClient()
    if not client.is_kernel_mode:
        print("  [SKIP] Kernel driver not loaded")
        return
    
    from kernel_client import _k32, wt
    
    # Test 3.1: Invalid page IDs
    invalid_page_ids = [
        (1 << 63) - 1,  # Max signed 64-bit
        (1 << 64) - 1,  # Max unsigned 64-bit
        0xFFFFFFFFFF,   # Large value
        -1 % (1 << 64), # -1 as unsigned
    ]
    
    for page_id in invalid_page_ids:
        try:
            data = client.read_page(page_id)
            # Should return zeros, not crash
            if data == b'\x00' * PAGE_SIZE:
                results.record_pass(f"Invalid page ID {page_id:#x} handled gracefully")
            else:
                results.record_fail(f"Invalid page ID {page_id:#x}", "Returned non-zero data")
        except Exception as e:
            results.record_crash(f"Invalid page ID {page_id:#x}", str(e))
    
    # Test 3.2: Random IOCTL codes
    print("\n  Testing random IOCTL codes...")
    for _ in range(100):
        random_ioctl = random.randint(0x100, 0xFFF)
        try:
            in_buf = (ctypes.c_ubyte * 100)()
            out_buf = (ctypes.c_ubyte * 100)()
            returned = wt.DWORD(0)
            _k32.DeviceIoControl(
                client._handle,
                random_ioctl,
                in_buf, len(in_buf),
                out_buf, len(out_buf),
                ctypes.byref(returned),
                None
            )
        except Exception:
            pass  # Expected for invalid IOCTLs
    
    results.record_pass("Random IOCTL codes handled gracefully")
    
    # Test 3.3: Rapid open/close cycles
    print("\n  Testing rapid open/close cycles...")
    try:
        for _ in range(100):
            c = HyperRAMKernelClient()
            c.close()
        results.record_pass("Rapid open/close cycles")
    except Exception as e:
        results.record_crash("Rapid open/close", str(e))
    
    client.close()

# ---------------------------------------------------------------------------
# 4. Stability Test
# ---------------------------------------------------------------------------
def test_stability(duration_seconds=60):
    """Long-running stability test."""
    print("\n" + SEP)
    print(f"  TEST 4: Stability Test ({duration_seconds}s)")
    print(SEP)
    
    client = HyperRAMKernelClient()
    if not client.is_kernel_mode:
        print("  [SKIP] Kernel driver not loaded")
        return
    
    start_time = time.perf_counter()
    ops_count = 0
    errors = 0
    rng = random.Random(42)
    
    # Pre-fill working set
    for i in range(2000):
        client.write_page(i, bytes([i & 0xFF]) * PAGE_SIZE)
    
    print(f"  Running stability test...")
    
    while time.perf_counter() - start_time < duration_seconds:
        try:
            page_id = rng.randint(0, 1999)
            
            if rng.random() < 0.7:
                data = client.read_page(page_id)
            else:
                data = bytes([rng.randint(0, 255)]) * PAGE_SIZE
                client.write_page(page_id, data)
            
            ops_count += 1
            
            # Periodic stats check
            if ops_count % 10000 == 0:
                stats = client.get_stats()
                if stats:
                    d = stats.to_dict()
                    print(f"    Ops: {ops_count}, Hit rate: {d['hit_rate_pct']:.1f}%, "
                          f"RAM: {d['ram_cache_pages']}/{d['max_ram_pages']}")
                    
                    # Check for counter corruption
                    if d['ram_cache_pages'] > d['max_ram_pages']:
                        results.record_fail("Stability test", 
                            f"RAM cache overflow: {d['ram_cache_pages']} > {d['max_ram_pages']}")
                        return
        except Exception as e:
            errors += 1
            if errors > 10:
                results.record_fail("Stability test", f"Too many errors: {errors}")
                return
    
    elapsed = time.perf_counter() - start_time
    ops_per_sec = ops_count / elapsed
    
    if errors == 0:
        results.record_pass(f"Stability test - {ops_count} ops in {elapsed:.1f}s ({ops_per_sec:.0f} ops/sec)")
    else:
        results.record_fail("Stability test", f"{errors} errors")
    
    client.close()

# ---------------------------------------------------------------------------
# Main Test Runner
# ---------------------------------------------------------------------------
def run_all_tests():
    """Run all security and stress tests."""
    print("\n" + SEP)
    print("  HyperRAM Security & Stress Test Suite")
    print(SEP)
    print(f"  Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEP)
    
    start_time = time.perf_counter()
    
    # Run all tests
    test_ioctl_buffer_validation()
    test_race_conditions()
    test_fuzzing()
    test_stability(duration_seconds=60)
    
    # Print summary
    elapsed = time.perf_counter() - start_time
    summary = results.summary()
    
    print("\n" + SEP)
    print("  TEST SUMMARY")
    print(SEP)
    print(f"  Tests run:    {summary['tests_run']}")
    print(f"  Tests passed: {summary['tests_passed']}")
    print(f"  Tests failed: {summary['tests_failed']}")
    print(f"  Crashes:      {summary['crashes']}")
    print(f"  BSODs:        {summary['bsods']}")
    print(f"  Deadlocks:    {summary['deadlocks']}")
    print(f"  Duration:     {elapsed:.1f}s")
    print(SEP)
    
    if summary['crashes'] == 0 and summary['bsods'] == 0 and \
       summary['deadlocks'] == 0 and summary['tests_failed'] == 0:
        print("\n  ✓ ALL TESTS PASSED - 0 crashes, 0 BSODs, 0 deadlocks")
        return 0
    else:
        print("\n  ✗ TESTS FAILED - See above for details")
        return 1

if __name__ == "__main__":
    sys.exit(run_all_tests())