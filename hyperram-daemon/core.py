import mmap
import os
# pyrefly: ignore [missing-import]
import lz4.block
import time
import math
from typing import Dict, Tuple

class HyperRAMEngine:
    def __init__(self, ssd_pool_path: str = "hyperram.pool", pool_size_gb: int = 16, page_size: int = 4096):
        self.ssd_pool_path = ssd_pool_path
        self.pool_size_bytes = pool_size_gb * 1024 * 1024 * 1024
        self.page_size = page_size
        self.num_pages = self.pool_size_bytes // self.page_size
        
        # Virtual Page Table: maps virtual page ID -> (is_in_ram, ram_buffer / ssd_offset, compressed_size)
        self.page_table: Dict[int, Tuple[bool, bytes, int]] = {}
        self.ram_cache: Dict[int, bytes] = {} # Hot pages in RAM
        self.max_ram_cache_pages = (2 * 1024 * 1024 * 1024) // self.page_size # 2GB RAM cache
        
        # Metrics
        self.total_reads = 0
        self.total_writes = 0
        self.ssd_reads = 0
        self.ssd_writes = 0
        self.total_compressed_bytes = 0
        self.total_uncompressed_bytes = 0
        self.cache_hits = 0
        self.cache_misses = 0

        self._init_pool()

    def _init_pool(self):
        # Create sparse file for the SSD pool
        if not os.path.exists(self.ssd_pool_path):
            with open(self.ssd_pool_path, "wb") as f:
                f.seek(self.pool_size_bytes - 1)
                f.write(b"\0")
        
        self.pool_file = open(self.ssd_pool_path, "r+b")
        self.ssd_mmap = mmap.mmap(self.pool_file.fileno(), self.pool_size_bytes)

    def write_page(self, page_id: int, data: bytes):
        if len(data) != self.page_size:
            data = data.ljust(self.page_size, b'\0')
            
        self.total_writes += 1
        
        # If RAM cache is full, evict a page to SSD
        if len(self.ram_cache) >= self.max_ram_cache_pages and page_id not in self.ram_cache:
            self._evict_page()
            
        # Store in RAM cache (Hot)
        self.ram_cache[page_id] = data
        self.page_table[page_id] = (True, b"", 0) # True = in RAM

    def read_page(self, page_id: int) -> bytes:
        self.total_reads += 1
        
        if page_id not in self.page_table:
            return b'\0' * self.page_size
            
        is_in_ram, _, compressed_size = self.page_table[page_id]
        
        if is_in_ram:
            self.cache_hits += 1
            return self.ram_cache[page_id]
            
        # Page is on SSD (Cold), need to read & decompress
        self.cache_misses += 1
        self.ssd_reads += 1
        
        ssd_offset = page_id * self.page_size
        self.ssd_mmap.seek(ssd_offset)
        compressed_data = self.ssd_mmap.read(compressed_size)
        
        # Decompress
        data = lz4.block.decompress(compressed_data, uncompressed_size=self.page_size)
        
        # Promote to RAM cache (Pre-fetching / Hot prediction)
        if len(self.ram_cache) >= self.max_ram_cache_pages:
            self._evict_page()
            
        self.ram_cache[page_id] = data
        self.page_table[page_id] = (True, b"", 0)
        
        return data

    def _evict_page(self):
        # Evict the oldest/least used page (simplified to popitem)
        evict_id, evict_data = self.ram_cache.popitem()
        
        # Compress
        compressed_data = lz4.block.compress(evict_data, store_size=False)
        self.total_uncompressed_bytes += len(evict_data)
        self.total_compressed_bytes += len(compressed_data)
        
        # Write to SSD pool (using mapped offset)
        self.ssd_writes += 1
        ssd_offset = evict_id * self.page_size
        self.ssd_mmap.seek(ssd_offset)
        self.ssd_mmap.write(compressed_data)
        
        # Update page table
        self.page_table[evict_id] = (False, b"", len(compressed_data))

    def get_metrics(self) -> dict:
        total_access = self.cache_hits + self.cache_misses
        hit_rate = (self.cache_hits / total_access) * 100 if total_access > 0 else 100.0
        compression_ratio = (self.total_uncompressed_bytes / self.total_compressed_bytes) if self.total_compressed_bytes > 0 else 1.0
        
        # Calculate simulated latency (bridging the gap)
        # Nanoseconds for RAM hit, Microseconds for SSD hit
        ram_latency_ns = 50 
        ssd_latency_ns = 25000 
        effective_latency_ns = (hit_rate/100 * ram_latency_ns) + ((100-hit_rate)/100 * ssd_latency_ns)

        return {
            "ram_used_mb": (len(self.ram_cache) * self.page_size) / (1024 * 1024),
            "ssd_used_mb": (len(self.page_table) - len(self.ram_cache)) * self.page_size / (1024 * 1024),
            "hit_rate_percent": round(hit_rate, 2),
            "compression_ratio": round(compression_ratio, 2),
            "ssd_writes": self.ssd_writes,
            "ssd_reads": self.ssd_reads,
            "effective_latency_ns": round(effective_latency_ns, 2)
        }

    def close(self):
        self.ssd_mmap.close()
        self.pool_file.close()
