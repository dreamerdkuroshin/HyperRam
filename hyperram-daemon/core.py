import mmap
import os
import gc
from collections import OrderedDict
# pyrefly: ignore [missing-import]
import lz4.block
from typing import Dict, Tuple
import threading

class QoSTag:
    PHYSICS = "physics"   # Pinned to RAM
    STATE = "state"       # Pinned to RAM
    TEXTURE = "texture"   # Bypasses RAM, goes straight to SSD
    SHADER = "shader"     # Spooled to SSD aggressively
    AI = "ai"             # Normal caching with prefetching
    DEFAULT = "default"

class HyperRAMEngine:
    def __init__(self, ssd_pool_path: str = "hyperram.pool", pool_size_gb: int = 2, page_size: int = 4096):
        self.ssd_pool_path = ssd_pool_path
        self.pool_size_gb = pool_size_gb
        self.pool_size_bytes = pool_size_gb * 1024 * 1024 * 1024
        self.page_size = page_size
        self.max_pages = self.pool_size_bytes // self.page_size
        self.lock = threading.RLock()
        
        # Virtual Page Table: maps page ID -> (is_in_ram, qos_tag, compressed_size, ssd_offset)
        self.page_table: Dict[int, Tuple[bool, str, int, int]] = {}
        self.offset_to_page: Dict[int, int] = {} # Reverse mapping: offset -> page_id
        # FIX Bug-4: Use OrderedDict for true LRU eviction (move_to_end on access)
        self.ram_cache: OrderedDict = OrderedDict()
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

        # Tau-based predictor state
        import time
        self.time_module = time
        self.last_access_time = None
        self.inter_arrival_tau = 0.010  # in seconds (10ms)
        self.last_page_id = 0
        self.last_stride = 1
        self.stride_confidence = 0

        self._init_pool()

    def _init_pool(self):
        if not os.path.exists(self.ssd_pool_path):
            with open(self.ssd_pool_path, "wb") as f:
                f.seek(self.pool_size_bytes - 1)
                f.write(b"\0")
        
        self.pool_file = open(self.ssd_pool_path, "r+b")
        self.ssd_mmap = mmap.mmap(self.pool_file.fileno(), self.pool_size_bytes)

    def resize_pool(self, new_size_gb: int):
        """Dynamically expand or shrink the SSD Virtual RAM pool on the fly."""
        with self.lock:
            old_size = self.pool_size_bytes
            new_size = new_size_gb * 1024 * 1024 * 1024
            self.pool_size_gb = new_size_gb
            self.pool_size_bytes = new_size
            self.max_pages = self.pool_size_bytes // self.page_size
            
            # Close existing mappings (force GC on Windows to release file handles)
            self.ssd_mmap.close()
            self.pool_file.close()
            gc.collect()
            
            # Resize the actual file on disk
            with open(self.ssd_pool_path, "r+b") as f:
                if new_size > old_size:
                    f.seek(new_size - 1)
                    f.write(b'\0')
                f.truncate(new_size)
                
            # Re-establish mappings
            self.pool_file = open(self.ssd_pool_path, "r+b")
            self.ssd_mmap = mmap.mmap(self.pool_file.fileno(), self.pool_size_bytes)
            
            # Clean stale page_table entries if pool shrank
            if new_size < old_size:
                stale_ids = []
                for pid, (_, _, comp_sz, ssd_off) in self.page_table.items():
                    if not self.page_table[pid][0] and ssd_off + comp_sz > self.pool_size_bytes:
                        stale_ids.append(pid)
                for pid in stale_ids:
                    del self.page_table[pid]
                    self.ram_cache.pop(pid, None)
                self.offset_to_page = {v[3]: k for k, v in self.page_table.items() if not v[0]}
                    
            print(f"[Core] Resized Virtual Pool to {new_size_gb}GB")

    def write_page(self, page_id: int, data: bytes, qos_tag: str = QoSTag.DEFAULT):
        with self.lock:
            if len(data) != self.page_size:
                data = data.ljust(self.page_size, b'\0')
                
            self.total_writes += 1
            self.qos_traffic[qos_tag] += 1
            
            # Texture Bypass Logic (DirectStorage Simulation)
            if qos_tag == QoSTag.TEXTURE:
                self._write_to_ssd(page_id, data, qos_tag)
                return

            # RAM Cache Full check
            if len(self.ram_cache) >= self.max_ram_cache_pages and page_id not in self.ram_cache:
                self._evict_page()

            # Store in RAM cache (LRU: move to end = most recently used)
            self.ram_cache[page_id] = data
            self.ram_cache.move_to_end(page_id)
            self.page_table[page_id] = (True, qos_tag, 0, 0)
            
            # Aggressively spool shaders to SSD
            if qos_tag == QoSTag.SHADER:
                self._force_evict(page_id)

    def read_page(self, page_id: int) -> bytes:
        with self.lock:
            self.total_reads += 1
            
            if page_id not in self.page_table:
                return b'\0' * self.page_size
                
            is_in_ram, qos_tag, compressed_size, ssd_offset = self.page_table[page_id]
            self.qos_traffic[qos_tag] += 1

            # ---- TAU-BASED PREDICTIVE PREFETCHING (applied to ALL QoS tags including non-prefetchable) ----
            now = self.time_module.perf_counter()
            if self.last_access_time is not None:
                delta_t = now - self.last_access_time
                self.inter_arrival_tau = 0.85 * self.inter_arrival_tau + 0.15 * delta_t
            self.last_access_time = now

            current_stride = page_id - self.last_page_id
            if current_stride == self.last_stride:
                self.stride_confidence = min(8, self.stride_confidence + 1)
            else:
                self.stride_confidence = max(0, self.stride_confidence - 2)
                self.last_stride = current_stride
            self.last_page_id = page_id

            prefetch_depth = 0
            if self.stride_confidence >= 3 and self.last_stride != 0:
                prefetch_depth = min(8, max(1, int(0.012 / (self.inter_arrival_tau + 1e-6))))

            for d in range(1, prefetch_depth + 1):
                next_page = page_id + d * self.last_stride
                if next_page in self.page_table:
                    nxt_is_in_ram, nxt_qos, nxt_comp_sz, nxt_offset = self.page_table[next_page]
                    if not nxt_is_in_ram and (nxt_offset + nxt_comp_sz) <= self.pool_size_bytes:
                        self.ssd_reads += 1
                        self.ssd_mmap.seek(nxt_offset)
                        nxt_comp_data = self.ssd_mmap.read(nxt_comp_sz)
                        if nxt_comp_sz == self.page_size:
                            nxt_data = nxt_comp_data
                        else:
                            try:
                                nxt_data = lz4.block.decompress(nxt_comp_data, uncompressed_size=self.page_size)
                            except Exception:
                                nxt_data = nxt_comp_data
                        
                        if len(self.ram_cache) >= self.max_ram_cache_pages:
                            self._evict_page()

                        self.ram_cache[next_page] = nxt_data
                        self.ram_cache.move_to_end(next_page)  # LRU: prefetch goes to end
                        self.page_table[next_page] = (True, nxt_qos, 0, 0)

            if is_in_ram:
                self.cache_hits += 1
                data = self.ram_cache[page_id]
                self.ram_cache.move_to_end(page_id)  # LRU: mark as recently used
            else:
                # Page is on SSD
                self.cache_misses += 1
                self.ssd_reads += 1
                
                # Prevent crash if pool was shrank and offset is now invalid
                if ssd_offset + compressed_size > self.pool_size_bytes:
                    return b'\0' * self.page_size
                    
                self.ssd_mmap.seek(ssd_offset)
                compressed_data = self.ssd_mmap.read(compressed_size)
                
                # Handle uncompressed fallback
                if compressed_size == self.page_size:
                    data = compressed_data
                else:
                    try:
                        data = lz4.block.decompress(compressed_data, uncompressed_size=self.page_size)
                    except Exception:
                        data = compressed_data
                
                # Textures remain on SSD (simulate streaming directly to GPU)
                if qos_tag == QoSTag.TEXTURE:
                    return data
                    
                # Promote to RAM cache (LRU: newly promoted pages go to end)
                if len(self.ram_cache) >= self.max_ram_cache_pages:
                    self._evict_page()

                self.ram_cache[page_id] = data
                self.ram_cache.move_to_end(page_id)
                self.page_table[page_id] = (True, qos_tag, 0, 0)

            return data

    def _force_evict(self, page_id: int):
        with self.lock:
            if page_id in self.ram_cache:
                data = self.ram_cache.pop(page_id)
                qos_tag = self.page_table[page_id][1]
                self._write_to_ssd(page_id, data, qos_tag)

    def _evict_page(self):
        with self.lock:
            # FIX Bug-4 (LRU): evict the LEAST recently used non-pinned page.
            # FIX Bug-3: guard against an empty cache to avoid IndexError.
            if not self.ram_cache:
                return

            evict_id = None
            # Iterate from oldest (front) to newest (back) — OrderedDict LRU order
            for pid in self.ram_cache:
                qos_tag = self.page_table[pid][1]
                if qos_tag not in [QoSTag.PHYSICS, QoSTag.STATE]:
                    evict_id = pid
                    break

            if evict_id is None:
                # All pages are pinned — evict the absolute oldest as last resort
                evict_id = next(iter(self.ram_cache))

            data = self.ram_cache.pop(evict_id)
            qos_tag = self.page_table[evict_id][1]
            self._write_to_ssd(evict_id, data, qos_tag)
            
    def _write_to_ssd(self, page_id: int, data: bytes, qos_tag: str):
        with self.lock:
            compressed_data = lz4.block.compress(data, store_size=False)
            
            # Enforce maximum boundary to prevent overwriting adjacent pages
            if len(compressed_data) > self.page_size:
                compressed_data = data
                
            self.total_uncompressed_bytes += len(data)
            self.total_compressed_bytes += len(compressed_data)
            self.ssd_writes += 1
            
            # Prevent boundary crash if pool was shrank
            safe_page_id = page_id % self.max_pages 
            ssd_offset = safe_page_id * self.page_size
            
            # Reverse mapping: prevent page ID collision when offset wraps around
            if ssd_offset in self.offset_to_page:
                old_page_id = self.offset_to_page[ssd_offset]
                if old_page_id != page_id and old_page_id in self.page_table:
                    del self.page_table[old_page_id]
            self.offset_to_page[ssd_offset] = page_id
            
            self.ssd_mmap.seek(ssd_offset)
            self.ssd_mmap.write(compressed_data)
            self.page_table[page_id] = (False, qos_tag, len(compressed_data), ssd_offset)

    def get_metrics(self) -> dict:
        with self.lock:
            total_access = self.cache_hits + self.cache_misses
            hit_rate = (self.cache_hits / total_access) * 100 if total_access > 0 else 100.0
            compression_ratio = (self.total_uncompressed_bytes / self.total_compressed_bytes) if self.total_compressed_bytes > 0 else 1.0
            ram_latency_ns = 50 
            ssd_latency_ns = 25000 
            effective_latency_ns = (hit_rate/100 * ram_latency_ns) + ((100-hit_rate)/100 * ssd_latency_ns)

            pinned_ram = sum(1 for pid in self.ram_cache if self.page_table[pid][1] in [QoSTag.PHYSICS, QoSTag.STATE])
            
            return {
                "ram_used_mb": (len(self.ram_cache) * self.page_size) / (1024 * 1024),
                "ssd_used_mb": (len(self.page_table) - len(self.ram_cache)) * self.page_size / (1024 * 1024),
                "pool_size_gb": self.pool_size_bytes / (1024**3),
                "hit_rate_percent": round(hit_rate, 2),
                "compression_ratio": round(compression_ratio, 2),
                "ssd_writes": self.ssd_writes,
                "ssd_reads": self.ssd_reads,
                "effective_latency_ns": round(effective_latency_ns, 2),
                "pinned_pages": pinned_ram,
                "qos_traffic": self.qos_traffic
            }

    def save_checkpoint(self, meta_path: str = None) -> str:
        """
        Persist all SSD-resident page metadata to a JSON sidecar file so the
        engine can survive a process restart without re-scanning the pool.

        Args:
            meta_path: Optional explicit path. Defaults to <pool_path>.meta.json
        Returns:
            Path of the written checkpoint file.
        """
        import json, time, hashlib
        if meta_path is None:
            meta_path = self.ssd_pool_path + ".meta.json"

        with self.lock:
            # Only serialise pages currently on SSD (in-RAM pages are transient)
            serialised = {}
            for pid, entry in self.page_table.items():
                is_in_ram, qos, comp_sz, ssd_off = entry
                if not is_in_ram:
                    serialised[str(pid)] = [False, qos, comp_sz, ssd_off]

            # Quick checksum of the first 64 KB of the pool file
            pool_checksum = "N/A"
            try:
                self.ssd_mmap.seek(0)
                sample = self.ssd_mmap.read(min(65536, self.pool_size_bytes))
                pool_checksum = hashlib.sha256(sample).hexdigest()
            except Exception:
                pass

            meta = {
                "saved_at":      time.strftime("%Y-%m-%d %H:%M:%S"),
                "pool_path":     self.ssd_pool_path,
                "pool_size_gb":  self.pool_size_gb,
                "page_size":     self.page_size,
                "total_pages":   len(serialised),
                "pool_checksum": pool_checksum,
                "metrics":       self.get_metrics(),
                "page_table":    serialised,
            }
        
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        return meta_path

    def load_checkpoint(self, meta_path: str = None) -> int:
        """
        Restore page_table from a previously saved checkpoint so pages on the
        NVMe pool can be found after a process restart.

        Args:
            meta_path: Optional explicit path. Defaults to <pool_path>.meta.json
        Returns:
            Number of pages restored, or 0 if no checkpoint found.
        """
        import json, hashlib
        if meta_path is None:
            meta_path = self.ssd_pool_path + ".meta.json"
        if not os.path.exists(meta_path):
            return 0
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        with self.lock:
            # Warn if pool has changed since checkpoint
            try:
                self.ssd_mmap.seek(0)
                sample = self.ssd_mmap.read(min(65536, self.pool_size_bytes))
                cksum = hashlib.sha256(sample).hexdigest()
                if cksum != meta.get("pool_checksum", ""):
                    print("  [core] WARNING: pool checksum mismatch after load_checkpoint. "
                          "Data may be inconsistent.")
            except Exception:
                pass

            restored = 0
            for pid_str, entry in meta["page_table"].items():
                pid = int(pid_str)
                is_in_ram, qos, comp_sz, ssd_off = entry
                self.page_table[pid] = (False, qos, comp_sz, ssd_off)
                self.offset_to_page[ssd_off] = pid
                restored += 1
        return restored

    def close(self):
        with self.lock:
            self.ssd_mmap.close()
            self.pool_file.close()
