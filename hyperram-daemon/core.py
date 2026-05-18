import mmap
import os
import lz4.block
from typing import Dict, Tuple

class QoSTag:
    PHYSICS = "physics"   # Pinned to RAM
    STATE = "state"       # Pinned to RAM
    TEXTURE = "texture"   # Bypasses RAM, goes straight to SSD
    SHADER = "shader"     # Spooled to SSD aggressively
    AI = "ai"             # Normal caching with prefetching
    DEFAULT = "default"

class HyperRAMEngine:
    def __init__(self, ssd_pool_path: str = "hyperram.pool", pool_size_gb: int = 16, page_size: int = 4096):
        self.ssd_pool_path = ssd_pool_path
        self.pool_size_bytes = pool_size_gb * 1024 * 1024 * 1024
        self.page_size = page_size
        
        # Virtual Page Table: maps page ID -> (is_in_ram, qos_tag, compressed_size, ssd_offset)
        self.page_table: Dict[int, Tuple[bool, str, int, int]] = {}
        self.ram_cache: Dict[int, bytes] = {} # Hot pages in RAM
        self.max_ram_cache_pages = (1 * 1024 * 1024 * 1024) // self.page_size # 1GB RAM cache for simulation
        
        # Metrics
        self.total_reads = 0
        self.total_writes = 0
        self.ssd_reads = 0
        self.ssd_writes = 0
        self.total_compressed_bytes = 0
        self.total_uncompressed_bytes = 0
        self.cache_hits = 0
        self.cache_misses = 0
        
        # QoS Traffic Counters
        self.qos_traffic = {
            QoSTag.PHYSICS: 0,
            QoSTag.STATE: 0,
            QoSTag.TEXTURE: 0,
            QoSTag.SHADER: 0,
            QoSTag.AI: 0,
            QoSTag.DEFAULT: 0
        }

        self._init_pool()

    def _init_pool(self):
        if not os.path.exists(self.ssd_pool_path):
            with open(self.ssd_pool_path, "wb") as f:
                f.seek(self.pool_size_bytes - 1)
                f.write(b"\0")
        
        self.pool_file = open(self.ssd_pool_path, "r+b")
        self.ssd_mmap = mmap.mmap(self.pool_file.fileno(), self.pool_size_bytes)

    def write_page(self, page_id: int, data: bytes, qos_tag: str = QoSTag.DEFAULT):
        if len(data) != self.page_size:
            data = data.ljust(self.page_size, b'\0')
            
        self.total_writes += 1
        self.qos_traffic[qos_tag] += 1
        
        # Texture Bypass Logic (DirectStorage Simulation)
        if qos_tag == QoSTag.TEXTURE:
            compressed_data = lz4.block.compress(data, store_size=False)
            self.total_uncompressed_bytes += len(data)
            self.total_compressed_bytes += len(compressed_data)
            self.ssd_writes += 1
            ssd_offset = page_id * self.page_size
            self.ssd_mmap.seek(ssd_offset)
            self.ssd_mmap.write(compressed_data)
            self.page_table[page_id] = (False, qos_tag, len(compressed_data), ssd_offset)
            return

        # RAM Cache Full check
        if len(self.ram_cache) >= self.max_ram_cache_pages and page_id not in self.ram_cache:
            self._evict_page()
            
        # Store in RAM cache
        self.ram_cache[page_id] = data
        self.page_table[page_id] = (True, qos_tag, 0, 0)
        
        # Aggressively spoof shaders to SSD in the background (we simulate this by immediately evicting)
        if qos_tag == QoSTag.SHADER:
            self._force_evict(page_id)

    def read_page(self, page_id: int) -> bytes:
        self.total_reads += 1
        
        if page_id not in self.page_table:
            return b'\0' * self.page_size
            
        is_in_ram, qos_tag, compressed_size, ssd_offset = self.page_table[page_id]
        self.qos_traffic[qos_tag] += 1
        
        if is_in_ram:
            self.cache_hits += 1
            return self.ram_cache[page_id]
            
        # Page is on SSD
        self.cache_misses += 1
        self.ssd_reads += 1
        
        self.ssd_mmap.seek(ssd_offset)
        compressed_data = self.ssd_mmap.read(compressed_size)
        data = lz4.block.decompress(compressed_data, uncompressed_size=self.page_size)
        
        # Textures remain on SSD (simulate streaming directly to GPU)
        if qos_tag == QoSTag.TEXTURE:
            return data
            
        # Promote to RAM cache
        if len(self.ram_cache) >= self.max_ram_cache_pages:
            self._evict_page()
            
        self.ram_cache[page_id] = data
        self.page_table[page_id] = (True, qos_tag, 0, 0)
        return data

    def _force_evict(self, page_id: int):
        if page_id in self.ram_cache:
            data = self.ram_cache.pop(page_id)
            qos_tag = self.page_table[page_id][1]
            self._write_to_ssd(page_id, data, qos_tag)

    def _evict_page(self):
        # Evict page that is NOT Physics or State (pinned)
        evict_id = None
        for pid, _ in self.ram_cache.items():
            qos_tag = self.page_table[pid][1]
            if qos_tag not in [QoSTag.PHYSICS, QoSTag.STATE]:
                evict_id = pid
                break
                
        if evict_id is None:
            # Fallback if everything is somehow pinned (shouldn't happen in a proper sized cache)
            evict_id = list(self.ram_cache.keys())[0]

        data = self.ram_cache.pop(evict_id)
        qos_tag = self.page_table[evict_id][1]
        self._write_to_ssd(evict_id, data, qos_tag)
        
    def _write_to_ssd(self, page_id: int, data: bytes, qos_tag: str):
        compressed_data = lz4.block.compress(data, store_size=False)
        self.total_uncompressed_bytes += len(data)
        self.total_compressed_bytes += len(compressed_data)
        self.ssd_writes += 1
        ssd_offset = page_id * self.page_size
        self.ssd_mmap.seek(ssd_offset)
        self.ssd_mmap.write(compressed_data)
        self.page_table[page_id] = (False, qos_tag, len(compressed_data), ssd_offset)

    def get_metrics(self) -> dict:
        total_access = self.cache_hits + self.cache_misses
        hit_rate = (self.cache_hits / total_access) * 100 if total_access > 0 else 100.0
        compression_ratio = (self.total_uncompressed_bytes / self.total_compressed_bytes) if self.total_compressed_bytes > 0 else 1.0
        ram_latency_ns = 50 
        ssd_latency_ns = 25000 
        effective_latency_ns = (hit_rate/100 * ram_latency_ns) + ((100-hit_rate)/100 * ssd_latency_ns)

        # Calculate pinned vs unpinned in RAM
        pinned_ram = sum(1 for pid in self.ram_cache if self.page_table[pid][1] in [QoSTag.PHYSICS, QoSTag.STATE])
        
        return {
            "ram_used_mb": (len(self.ram_cache) * self.page_size) / (1024 * 1024),
            "ssd_used_mb": (len(self.page_table) - len(self.ram_cache)) * self.page_size / (1024 * 1024),
            "hit_rate_percent": round(hit_rate, 2),
            "compression_ratio": round(compression_ratio, 2),
            "ssd_writes": self.ssd_writes,
            "ssd_reads": self.ssd_reads,
            "effective_latency_ns": round(effective_latency_ns, 2),
            "pinned_pages": pinned_ram,
            "qos_traffic": self.qos_traffic
        }

    def close(self):
        self.ssd_mmap.close()
        self.pool_file.close()
