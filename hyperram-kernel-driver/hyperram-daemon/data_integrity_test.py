# -*- coding: utf-8 -*-
r"""
============================================================================
  data_integrity_test.py — HyperRAM Data Integrity Validation
============================================================================
  Comprehensive data integrity testing to detect:
    - Page corruption during compression/decompression
    - Race conditions in page table updates
    - Eviction of pages still in use
    - Memory-mapped file handling errors
  
  Tests:
    1. Write-Read-Verify (1M pages)
    2. Concurrent Access (64 threads)
    3. Long-Running Eviction Test
    4. Pattern Stress Test (edge cases)
    5. Hash Verification Under Load
  
  Usage:
    python data_integrity_test.py --test all
    python data_integrity_test.py --test write-read --pages 1000000
    python data_integrity_test.py --test concurrent --threads 64
    python data_integrity_test.py --test eviction --duration 10m
============================================================================
"""
import sys, os, json, time, hashlib, threading, random, statistics
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))
from kernel_client import HyperRAMKernelClient

PAGE_SIZE = 4096
SEP = "=" * 72
DASH = "-" * 72

class DataIntegrityTester:
    """Comprehensive data integrity validation."""
    
    def __init__(self, output_dir='results'):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.client = HyperRAMKernelClient()
        self.errors = []
        self.errors_lock = threading.Lock()
        
        self.results = {
            'test_type': None,
            'start_time': None,
            'end_time': None,
            'duration_sec': 0,
            'pages_tested': 0,
            'operations': 0,
            'errors': [],
            'corruptions': 0,
            'success_rate': 100.0,
            'threads_used': 1,
        }
    
    def add_error(self, error_msg):
        """Thread-safe error logging."""
        with self.errors_lock:
            self.errors.append(error_msg)
            if len(self.errors) <= 100:  # Limit stored errors
                print(f"    ERROR: {error_msg}")
    
    def generate_test_pattern(self, page_id, pattern_type='random'):
        """Generate test data patterns."""
        if pattern_type == 'random':
            return bytes([random.randint(0, 255) for _ in range(PAGE_SIZE)])
        elif pattern_type == 'sequential':
            return bytes([(page_id + i) & 0xFF for i in range(PAGE_SIZE)])
        elif pattern_type == 'zeros':
            return bytes(PAGE_SIZE)
        elif pattern_type == 'ones':
            return bytes([0xFF] * PAGE_SIZE)
        elif pattern_type == 'alternating':
            return bytes([0xAA if i % 2 == 0 else 0x55 for i in range(PAGE_SIZE)])
        elif pattern_type == 'checksum':
            # Pattern with embedded checksum
            data = bytearray(PAGE_SIZE)
            for i in range(PAGE_SIZE - 4):
                data[i] = (page_id + i) & 0xFF
            checksum = hash(page_id) & 0xFFFFFFFF
            data[PAGE_SIZE-4:PAGE_SIZE] = checksum.to_bytes(4, 'little')
            return bytes(data)
        else:
            return bytes([page_id & 0xFF] * PAGE_SIZE)
    
    def verify_pattern(self, page_id, data, pattern_type='random', expected_hash=None):
        """Verify data integrity."""
        if expected_hash:
            actual_hash = hashlib.sha256(data).hexdigest()
            if actual_hash != expected_hash:
                return False, f"Hash mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
        
        if pattern_type == 'checksum':
            # Verify embedded checksum
            stored_checksum = int.from_bytes(data[PAGE_SIZE-4:PAGE_SIZE], 'little')
            computed = hash(page_id) & 0xFFFFFFFF
            if stored_checksum != computed:
                return False, f"Checksum mismatch for page {page_id}"
        
        return True, None
    
    def test_write_read_verify(self, num_pages=100000, pattern_type='random'):
        """
        Test 1: Write-Read-Verify
        
        Writes num_pages, then reads back and verifies.
        Tests: compression/decompression, basic I/O path
        """
        print("\n" + SEP)
        print("  Test 1: Write-Read-Verify")
        print(SEP)
        print(f"  Pages: {num_pages:,}")
        print(f"  Pattern: {pattern_type}")
        print(DASH)
        
        self.results['test_type'] = 'write_read_verify'
        self.results['start_time'] = datetime.now().isoformat()
        self.errors = []
        
        # Write phase
        print("\n  Phase 1: Writing pages...")
        hashes = {}
        write_start = time.perf_counter()
        
        for i in range(num_pages):
            try:
                data = self.generate_test_pattern(i, pattern_type)
                hashes[i] = hashlib.sha256(data).hexdigest()
                self.client.write_page(i, data)
                
                if (i + 1) % 10000 == 0:
                    print(f"    Written {i+1:,} pages...")
                    
            except Exception as e:
                self.add_error(f"Write failed for page {i}: {e}")
        
        write_elapsed = time.perf_counter() - write_start
        print(f"  Write complete: {write_elapsed:.1f}s ({num_pages/write_elapsed/1000:.1f}K pages/sec)")
        
        # Read phase
        print("\n  Phase 2: Reading and verifying pages...")
        read_start = time.perf_counter()
        corruptions = 0
        
        for i in range(num_pages):
            try:
                data = self.client.read_page(i)
                expected_hash = hashes.get(i)
                
                if expected_hash:
                    actual_hash = hashlib.sha256(data).hexdigest()
                    if actual_hash != expected_hash:
                        corruptions += 1
                        self.add_error(f"Page {i}: CORRUPTION detected (hash mismatch)")
                
                if (i + 1) % 10000 == 0:
                    print(f"    Verified {i+1:,} pages (corruptions: {corruptions})...")
                    
            except Exception as e:
                corruptions += 1
                self.add_error(f"Read failed for page {i}: {e}")
        
        read_elapsed = time.perf_counter() - read_start
        print(f"  Read complete: {read_elapsed:.1f}s ({num_pages/read_elapsed/1000:.1f}K pages/sec)")
        
        # Summary
        self.results['end_time'] = datetime.now().isoformat()
        self.results['duration_sec'] = write_elapsed + read_elapsed
        self.results['pages_tested'] = num_pages
        self.results['operations'] = num_pages * 2  # write + read
        self.results['corruptions'] = corruptions
        self.results['errors'] = self.errors[:50]
        
        success_rate = ((num_pages - corruptions) / num_pages * 100) if num_pages > 0 else 0
        self.results['success_rate'] = success_rate
        
        print("\n" + DASH)
        print("  Results:")
        print(f"    Total Pages: {num_pages:,}")
        print(f"    Duration: {self.results['duration_sec']:.1f}s")
        print(f"    Corruptions: {corruptions:,}")
        print(f"    Success Rate: {success_rate:.4f}%")
        print(f"    Write Throughput: {num_pages/write_elapsed/1000:.1f}K pages/sec")
        print(f"    Read Throughput: {num_pages/read_elapsed/1000:.1f}K pages/sec")
        
        if corruptions > 0:
            print(f"\n  ✗ DATA CORRUPTION DETECTED: {corruptions} pages")
        else:
            print(f"\n  ✓ DATA INTEGRITY VERIFIED: {num_pages:,} pages")
        print(DASH)
        
        return corruptions == 0
    
    def test_concurrent_access(self, num_pages=10000, num_threads=64, operations_per_thread=100):
        """
        Test 2: Concurrent Access
        
        Multiple threads reading/writing simultaneously.
        Tests: race conditions, lock contention, page table consistency
        """
        print("\n" + SEP)
        print("  Test 2: Concurrent Access")
        print(SEP)
        print(f"  Pages: {num_pages:,}")
        print(f"  Threads: {num_threads}")
        print(f"  Ops/Thread: {operations_per_thread}")
        print(DASH)
        
        self.results['test_type'] = 'concurrent_access'
        self.results['start_time'] = datetime.now().isoformat()
        self.errors = []
        
        # Pre-load pages
        print("\n  Pre-loading pages...")
        for i in range(num_pages):
            data = self.generate_test_pattern(i, 'sequential')
            self.client.write_page(i, data)
        
        # Worker function
        def worker(thread_id):
            thread_errors = []
            ops_count = 0
            
            for op in range(operations_per_thread):
                try:
                    page_id = random.randint(0, num_pages - 1)
                    
                    if random.random() < 0.7:  # 70% reads
                        data = self.client.read_page(page_id)
                        # Verify sequential pattern
                        expected = bytes([(page_id + i) & 0xFF for i in range(PAGE_SIZE)])
                        if data != expected:
                            thread_errors.append(f"Thread {thread_id}: Page {page_id} data mismatch")
                    else:  # 30% writes
                        new_data = self.generate_test_pattern(page_id + thread_id, 'random')
                        self.client.write_page(page_id, new_data)
                    
                    ops_count += 1
                    
                except Exception as e:
                    thread_errors.append(f"Thread {thread_id} op {op}: {e}")
            
            return ops_count, thread_errors
        
        # Run concurrent test
        print(f"\n  Starting {num_threads} threads...")
        start_time = time.perf_counter()
        
        total_ops = 0
        all_errors = []
        
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, tid) for tid in range(num_threads)]
            
            for future in as_completed(futures):
                ops, errors = future.result()
                total_ops += ops
                all_errors.extend(errors)
        
        elapsed = time.perf_counter() - start_time
        
        # Update results
        self.results['end_time'] = datetime.now().isoformat()
        self.results['duration_sec'] = elapsed
        self.results['operations'] = total_ops
        self.results['pages_tested'] = num_pages
        self.results['threads_used'] = num_threads
        self.results['errors'] = all_errors[:50]
        self.results['corruptions'] = len([e for e in all_errors if 'mismatch' in e])
        
        success_rate = ((total_ops - len(all_errors)) / total_ops * 100) if total_ops > 0 else 0
        self.results['success_rate'] = success_rate
        
        print(f"\n  Complete: {elapsed:.1f}s")
        print(f"  Total Operations: {total_ops:,}")
        print(f"  Throughput: {total_ops/elapsed/1000:.1f}K ops/sec")
        print(f"  Errors: {len(all_errors)}")
        
        if all_errors:
            print(f"\n  ✗ {len(all_errors)} ERRORS DETECTED")
            for err in all_errors[:10]:
                print(f"    - {err}")
        else:
            print(f"\n  ✓ NO ERRORS - Concurrent access safe")
        
        print(DASH)
        
        return len(all_errors) == 0
    
    def test_eviction_under_load(self, duration_min=10, pages=1000):
        """
        Test 3: Eviction Under Load
        
        Continuous read/write while cache is under pressure.
        Tests: eviction correctness, pages-in-use protection
        """
        print("\n" + SEP)
        print("  Test 3: Eviction Under Load")
        print(SEP)
        print(f"  Duration: {duration_min} minutes")
        print(f"  Active Pages: {pages}")
        print(DASH)
        
        self.results['test_type'] = 'eviction_load'
        self.results['start_time'] = datetime.now().isoformat()
        self.errors = []
        
        # Initialize pages
        print("\n  Initializing pages...")
        hashes = {}
        for i in range(pages):
            data = self.generate_test_pattern(i, 'checksum')
            hashes[i] = hashlib.sha256(data).hexdigest()
            self.client.write_page(i, data)
        
        print(f"  Written {pages} pages with checksums")
        
        # Continuous access loop
        print(f"\n  Running continuous access for {duration_min} minutes...")
        start_time = time.perf_counter()
        max_duration_sec = duration_min * 60
        
        ops_count = 0
        corruptions = 0
        check_interval = 1000
        
        while time.perf_counter() - start_time < max_duration_sec:
            # Random access
            page_id = random.randint(0, pages - 1)
            
            try:
                # Read and verify
                data = self.client.read_page(page_id)
                expected_hash = hashes.get(page_id)
                
                if expected_hash:
                    actual_hash = hashlib.sha256(data).hexdigest()
                    if actual_hash != expected_hash:
                        corruptions += 1
                        self.add_error(f" Corruption: Page {page_id} hash mismatch")
                
                # Occasionally rewrite
                if random.random() < 0.1:  # 10% rewrites
                    new_data = self.generate_test_pattern(page_id, 'checksum')
                    hashes[page_id] = hashlib.sha256(new_data).hexdigest()
                    self.client.write_page(page_id, new_data)
                
                ops_count += 1
                
                # Periodic status
                if ops_count % check_interval == 0:
                    elapsed = time.perf_counter() - start_time
                    print(f"    Ops: {ops_count:,}, Corruptions: {corruptions}, Elapsed: {elapsed:.0f}s")
                    
            except Exception as e:
                self.add_error(f"Operation failed: {e}")
        
        elapsed = time.perf_counter() - start_time
        
        # Final verification
        print("\n  Final verification pass...")
        final_corruptions = 0
        for i in range(pages):
            try:
                data = self.client.read_page(i)
                expected_hash = hashes.get(i)
                
                if expected_hash:
                    actual_hash = hashlib.sha256(data).hexdigest()
                    if actual_hash != expected_hash:
                        final_corruptions += 1
                        
            except:
                final_corruptions += 1
        
        # Update results
        self.results['end_time'] = datetime.now().isoformat()
        self.results['duration_sec'] = elapsed
        self.results['operations'] = ops_count
        self.results['pages_tested'] = pages
        self.results['corruptions'] = corruptions + final_corruptions
        self.results['errors'] = self.errors[:50]
        
        success_rate = ((ops_count - len(self.errors)) / ops_count * 100) if ops_count > 0 else 0
        self.results['success_rate'] = success_rate
        
        print("\n" + DASH)
        print("  Results:")
        print(f"    Duration: {elapsed:.1f}s")
        print(f"    Operations: {ops_count:,}")
        print(f"    In-Test Corruptions: {corruptions}")
        print(f"    Final Verification Corruptions: {final_corruptions}")
        print(f"    Total Corruptions: {corruptions + final_corruptions}")
        
        if corruptions + final_corruptions > 0:
            print(f"\n  ✗ EVICTION BUG DETECTED: {corruptions + final_corruptions} pages corrupted")
        else:
            print(f"\n  ✓ EVICTION SAFE: No corruption under load")
        print(DASH)
        
        return (corruptions + final_corruptions) == 0
    
    def test_pattern_stress(self):
        """
        Test 4: Pattern Stress Test
        
        Tests edge case patterns that often reveal compression bugs.
        """
        print("\n" + SEP)
        print("  Test 4: Pattern Stress Test")
        print(SEP)
        print(DASH)
        
        self.results['test_type'] = 'pattern_stress'
        self.results['start_time'] = datetime.now().isoformat()
        self.errors = []
        
        # Problematic patterns for compression algorithms
        patterns = [
            ('all_zeros', bytes(PAGE_SIZE)),
            ('all_ones', bytes([0xFF] * PAGE_SIZE)),
            ('alternating', bytes([0xAA, 0x55] * (PAGE_SIZE // 2))),
            ('gradient', bytes([i & 0xFF for i in range(PAGE_SIZE)])),
            ('sparse', bytes([0xFF if i % 100 == 0 else 0x00 for i in range(PAGE_SIZE)])),
            ('repeating', bytes([0x41, 0x42, 0x43, 0x44] * (PAGE_SIZE // 4))),
        ]
        
        corruptions = 0
        total_tests = 0
        
        for pattern_name, pattern_data in patterns:
            print(f"\n  Testing pattern: {pattern_name}")
            
            # Write and read back
            for i in range(100):
                page_id = 1000 + total_tests
                try:
                    self.client.write_page(page_id, pattern_data)
                    read_data = self.client.read_page(page_id)
                    
                    expected_hash = hashlib.sha256(pattern_data).hexdigest()
                    actual_hash = hashlib.sha256(read_data).hexdigest()
                    
                    if actual_hash != expected_hash:
                        corruptions += 1
                        self.add_error(f"Pattern {pattern_name}, page {page_id}: corruption")
                    
                    total_tests += 1
                    
                except Exception as e:
                    corruptions += 1
                    self.add_error(f"Pattern {pattern_name} failed: {e}")
        
        self.results['end_time'] = datetime.now().isoformat()
        self.results['pages_tested'] = total_tests
        self.results['corruptions'] = corruptions
        self.results['errors'] = self.errors[:50]
        
        success_rate = ((total_tests - corruptions) / total_tests * 100) if total_tests > 0 else 0
        self.results['success_rate'] = success_rate
        
        print("\n" + DASH)
        print(f"  Patterns Tested: {len(patterns)}")
        print(f"  Total Pages: {total_tests}")
        print(f"  Corruptions: {corruptions}")
        
        if corruptions > 0:
            print(f"\n  ✗ PATTERN STRESS FAILED: {corruptions} corruptions")
        else:
            print(f"\n  ✓ PATTERN STRESS PASSED: All patterns verified")
        print(DASH)
        
        return corruptions == 0
    
    def run_all_tests(self, pages=100000, threads=64, duration_min=5):
        """Run complete test suite."""
        print("\n" + SEP)
        print("  Complete Data Integrity Test Suite")
        print(SEP)
        
        results_summary = []
        
        # Test 1
        passed = self.test_write_read_verify(num_pages=pages)
        results_summary.append(('Write-Read-Verify', passed))
        
        # Test 2
        passed = self.test_concurrent_access(num_pages=pages//10, num_threads=threads)
        results_summary.append(('Concurrent Access', passed))
        
        # Test 3
        passed = self.test_eviction_under_load(duration_min=duration_min, pages=pages//10)
        results_summary.append(('Eviction Under Load', passed))
        
        # Test 4
        passed = self.test_pattern_stress()
        results_summary.append(('Pattern Stress', passed))
        
        # Summary
        print("\n" + SEP)
        print("  TEST SUITE SUMMARY")
        print(SEP)
        
        all_passed = True
        for test_name, passed in results_summary:
            status = "✓ PASS" if passed else "✗ FAIL"
            print(f"    {test_name}: {status}")
            if not passed:
                all_passed = False
        
        print(SEP)
        
        if all_passed:
            print("\n  ✓ ALL INTEGRITY TESTS PASSED")
        else:
            print("\n  ✗ SOME TESTS FAILED - Review errors above")
        
        return all_passed
    
    def save_results(self):
        """Save results to JSON."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        json_path = self.output_dir / f'data_integrity_{timestamp}.json'
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, default=str)
        
        print(f"\n  Results saved to: {json_path}")
        return json_path
    
    def close(self):
        """Cleanup."""
        self.client.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='HyperRAM Data Integrity Test')
    parser.add_argument('--test', type=str, default='all',
                       choices=['all', 'write-read', 'concurrent', 'eviction', 'pattern'],
                       help='Test to run')
    parser.add_argument('--pages', type=int, default=100000,
                       help='Number of pages to test')
    parser.add_argument('--threads', type=int, default=64,
                       help='Number of concurrent threads')
    parser.add_argument('--duration', type=int, default=5,
                       help='Duration in minutes (for eviction test)')
    parser.add_argument('--output', type=str, default='results',
                       help='Output directory')
    
    args = parser.parse_args()
    
    tester = DataIntegrityTester(output_dir=args.output)
    
    try:
        if args.test == 'all':
            tester.run_all_tests(pages=args.pages, threads=args.threads, duration_min=args.duration)
        elif args.test == 'write-read':
            tester.test_write_read_verify(num_pages=args.pages)
        elif args.test == 'concurrent':
            tester.test_concurrent_access(num_pages=args.pages//10, num_threads=args.threads)
        elif args.test == 'eviction':
            tester.test_eviction_under_load(duration_min=args.duration, pages=args.pages//10)
        elif args.test == 'pattern':
            tester.test_pattern_stress()
        
        tester.save_results()
    
    finally:
        tester.close()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())