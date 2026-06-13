import mmap
import os
import gc
import zlib
import time
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


class PageMetadata:
    """Page metadata with atomic reference counting for thread safety."""
    def __init__(self, page_id: int, qos_tag: str = QoSTag.DEFAULT):
        self.page_id = page_id
        self.qos_tag = qos_tag
        self.is_in_ram = False
        self.compressed_size = 0
        self.ssd_offset = 0
        self.original_size = 0
        self.crc32 = 0
        self.stored_size = 0
        self.ref_count = 0
        self.evicting = False
        self.access_count = 0
        self.last_access_time = time.perf_counter()
    
    def acquire(self):
        """Acquire a reference to the page (prevents eviction)."""
        self.ref_count += 1
    
    def release(self):
        """Release a reference to the page."""
        self.ref_count = max(0, self.ref_count - 1)
    
    def can_evict(self) -> bool:
        """Check if page can be safely evicted."""
        return self.ref_count == 0 and not self.evicting
    
    def update_access(self):
        """Update access metadata."""
        self.access_count += 1
        self.last_access_time = time.perf_counter()


class CompressedPage:
    """Compressed page with CRC32 checksum for integrity verification."""
    HEADER_SIZE = 16  # crc32(4) + compressed_size(4) + original_size(4) + flags(4)
    
    def __init__(self, data: bytes = None, compressed_data: bytes = None):
        if data:
            self.original_size = len(data)
            self.compressed_data = lz4.block.compress(data, store_size=False)
            self.compressed_size = len(self.compressed_data)
            self.crc32 = zlib.crc32(data) & 0xFFFFFFFF
        elif compressed_data:
            self.compressed_data = compressed_data
            self.compressed_size = len(compressed_data)
            self.original_size = 0
            self.crc32 = 0
            
    def to_bytes(self) -> bytes:
        """Serialize compressed page with header."""
        header = (
            self.crc32.to_bytes(4, 'little') +
            self.compressed_size.to_bytes(4, 'little') +
            self.original_size.to_bytes(4, 'little') +
            b'\x00\x00\x00\x00'  # flags (reserved)
        )
        return header + self.compressed_data
        
    @classmethod
    def from_bytes(cls, data: bytes) -> 'CompressedPage':
        """Deserialize compressed page from bytes."""
        if len(data) < cls.HEADER_SIZE:
            raise ValueError("Invalid compressed page: too small")
        crc32 = int.from_bytes(data[0:4], 'little')
        compressed_size = int.from_bytes(data[4:8], 'little')
        original_size = int.from_bytes(data[8:12], 'little')
        compressed_data = data[cls.HEADER_SIZE:cls.HEADER_SIZE + compressed_size]
        page = cls(compressed_data=compressed_data)
        page.crc32 = crc32
        page.original_size = original_size
        return page
        
    def decompress(self, expected_size: int = None) -> bytes:
        """Decompress and verify CRC32."""
        if self.original_size == 0 and expected_size:
            self.original_size = expected_size
        try:
            if self.compressed_size == self.original_size:
                data = self.compressed_data
            else:
                data = lz4.block.decompress(self.compressed_data, uncompressed_size=self.original_size)
        except Exception as e:
            raise ValueError(f"Decompression failed: {e}")
        if zlib.crc32(data) & 0xFFFFFFFF != self.crc32:
            raise ValueError(f"CRC32 mismatch: expected {self.crc32:08x}, got {zlib.crc32(data) & 0xFFFFFFFF:08x}")
        return data


class HyperRAMEngine:
    def __init__(self, ssd_pool_path: str = "hyperram.pool", pool_size_gb: int = 2, page_size: int = 4096):
        self.ssd_pool_path = ssd_pool_path
        self.pool_size_gb = pool_size_gb
        self.pool_size_bytes = pool_size_gb * 1024 * 1024 * 1024
        self.page_size = page_size
        self.max_pages = self.pool_size_bytes // self.page_size
        self.lock = threading.RLock()
        
        # Virtual Page Table: maps page ID -> (is_in_ram, qos_tag, compressed_size, ssd_offset)
        self.page_table: Dict[int, PageMetadata] = {}
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
        self.corruption_count = 0
        self.thrash_events = 0
        self.thrashing_threshold = 100  # faults per second
        self.fault_window = []  # (timestamp, fault_count)
        self.thrashing_detected = False
        self.adaptive_cache_enabled = True
        
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
                    if not meta.is_in_ram and meta.ssd_offset + meta.stored_size > self.pool_size_bytes:
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
            
            # Create or update page metadata
            if page_id not in self.page_table:
                self.page_table[page_id] = PageMetadata(page_id, qos_tag)
            meta = self.page_table[page_id]
            meta.is_in_ram = True
            meta.qos_tag = qos_tag
            meta.update_access()
            
            # Aggressively spool shaders to SSD
            if qos_tag == QoSTag.SHADER:
                self._force_evict(page_id)

    def read_page(self, page_id: int) -> bytes:
        with self.lock:
            self.total_reads += 1
            
            if page_id not in self.page_table:
                return b'\0' * self.page_size
                
            meta = self.page_table[page_id]
            qos_tag = meta.qos_tag
            self.qos_traffic[qos_tag] += 1
            
            # Acquire reference to prevent eviction during read
            meta.acquire()

            # ---- TAU-BASED PREDICTIVE PREFETCHING ----
            now = time.perf_counter()
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

            # TEMPORARILY DISABLE PREFETCHING to prevent invalid reads
            # Re-enable once metadata initialization is fixed
            prefetch_depth = 0

            for d in range(1, prefetch_depth + 1):
                next_page = page_id + d * self.last_stride
                if next_page in self.page_table:
                    nxt_meta = self.page_table[next_page]
                    if not nxt_meta.is_in_ram and (nxt_meta.ssd_offset + nxt_meta.stored_size) <= self.pool_size_bytes:
                        self.ssd_reads += 1
                        self._prefetch_page_from_ssd(next_page, nxt_meta)

            if meta.is_in_ram:
                self.cache_hits += 1
                data = self.ram_cache[page_id]
                self.ram_cache.move_to_end(page_id)
            else:
                # Page is on SSD
                self.cache_misses += 1
                self.ssd_reads += 1
                
                # Check for thrashing
                self._record_page_fault()
                
                # Prevent crash if pool was shrank and offset is now invalid
                if meta.ssd_offset + meta.stored_size > self.pool_size_bytes:
                    meta.release()
                    return b'\0' * self.page_size
                    
                # MMAP bounds validation
                if meta.ssd_offset < 0 or meta.ssd_offset + meta.stored_size > len(self.ssd_mmap):
                    meta.release()
                    raise ValueError(f"MMAP bounds violation: offset={meta.ssd_offset}, stored_size={meta.stored_size}, mmap_size={len(self.ssd_mmap)}")
                    
                self.ssd_mmap.seek(meta.ssd_offset)
                # Read the full compressed page (header + data)
                full_data = self.ssd_mmap.read(meta.stored_size)
                
                # Decompress with CRC32 validation
                try:
                    comp_page = CompressedPage.from_bytes(full_data)
                    data = comp_page.decompress(self.page_size)
                except ValueError as e:
                    self.corruption_count += 1
                    print(f"[Core] CRC32 validation failed for page {page_id}: {e}")
                    data = b'\0' * self.page_size
                
                # Textures remain on SSD
                if qos_tag == QoSTag.TEXTURE:
                    meta.release()
                    return data
                    
                # Promote to RAM cache
                if len(self.ram_cache) >= self.max_ram_cache_pages:
                    self._evict_page()

                self.ram_cache[page_id] = data
                self.ram_cache.move_to_end(page_id)
                meta.is_in_ram = True
                meta.compressed_size = 0
                meta.ssd_offset = 0
                meta.update_access()

            meta.release()
            meta.update_access()
            return data

    def _force_evict(self, page_id: int):
        with self.lock:
            if page_id in self.ram_cache:
                data = self.ram_cache.pop(page_id)
                if page_id in self.page_table:
                    meta = self.page_table[page_id]
                    # Only evict if no active references
                    if meta.can_evict():
                        meta.evicting = True
                        self._write_to_ssd(page_id, data, meta.qos_tag)
                        meta.evicting = False

    def _evict_page(self):
        with self.lock:
            # FIX Bug-4 (LRU): evict the LEAST recently used non-pinned page.
            # FIX Bug-3: guard against an empty cache to avoid IndexError.
            # FIX P0: Only evict pages with ref_count == 0
            if not self.ram_cache:
                return

            evict_id = None
            # Iterate from oldest (front) to newest (back) — OrderedDict LRU order
            for pid in self.ram_cache:
                if pid not in self.page_table:
                    continue
                meta = self.page_table[pid]
                if meta.qos_tag not in [QoSTag.PHYSICS, QoSTag.STATE] and meta.can_evict():
                    evict_id = pid
                    break

            if evict_id is None:
                # All pages are pinned or in-use — try to find any evictable page
                for pid in self.ram_cache:
                    if pid not in self.page_table:
                        continue
                    meta = self.page_table[pid]
                    if meta.can_evict():
                        evict_id = pid
                        break
                        
            if evict_id is None:
                # Cannot evict any page - thrashing risk
                self.thrash_events += 1
                return

            data = self.ram_cache.pop(evict_id)
            if evict_id in self.page_table:
                meta = self.page_table[evict_id]
                meta.evicting = True
                self._write_to_ssd(evict_id, data, meta.qos_tag)
                meta.evicting = False
            
    def _write_to_ssd(self, page_id: int, data: bytes, qos_tag: str):
        with self.lock:
            # Try to compress with CRC32
            comp_page = CompressedPage(data)
            total_size = CompressedPage.HEADER_SIZE + comp_page.compressed_size
            
            # If compressed data is larger than original or doesn't fit, store uncompressed
            if comp_page.compressed_size >= len(data) or total_size > self.page_size:
                # Store as uncompressed: set compressed_size = original_size
                # This signals decompress() to skip LZ4 and just return raw data
                comp_page.compressed_size = len(data)
                comp_page.compressed_data = data
                total_size = CompressedPage.HEADER_SIZE + len(data)
            
            compressed_data = comp_page.to_bytes()
                
            self.total_uncompressed_bytes += len(data)
            self.total_compressed_bytes += total_size
            self.ssd_writes += 1
            
            # Prevent boundary crash if pool was shrank
            safe_page_id = page_id % self.max_pages 
            # Use stride that accounts for header overhead to prevent overlap
            # Max stored size = page_size + HEADER_SIZE (for uncompressed data)
            stride = self.page_size + CompressedPage.HEADER_SIZE
            ssd_offset = safe_page_id * stride
            
            # MMAP bounds validation
            if ssd_offset + total_size > len(self.ssd_mmap):
                print(f"[Core] WARNING: SSD offset {ssd_offset} + size {total_size} exceeds mmap bounds {len(self.ssd_mmap)}")
                return
            
            # Reverse mapping: prevent page ID collision when offset wraps around
            if ssd_offset in self.offset_to_page:
                old_page_id = self.offset_to_page[ssd_offset]
                if old_page_id != page_id and old_page_id in self.page_table:
                    del self.page_table[old_page_id]
            self.offset_to_page[ssd_offset] = page_id
            
            self.ssd_mmap.seek(ssd_offset)
            self.ssd_mmap.write(compressed_data)
            
            # Update metadata
            if page_id in self.page_table:
                meta = self.page_table[page_id]
                meta.is_in_ram = False
                meta.compressed_size = comp_page.compressed_size if total_size <= self.page_size else len(data)
                meta.ssd_offset = ssd_offset
                meta.original_size = len(data)
                meta.crc32 = comp_page.crc32
                meta.stored_size = total_size


    def _prefetch_page_from_ssd(self, page_id: int, meta: PageMetadata):
        """Prefetch a page from SSD into RAM cache."""
        # MMAP bounds validation
        if meta.ssd_offset < 0 or meta.stored_size <= 0 or meta.ssd_offset + meta.stored_size > len(self.ssd_mmap):
            return
        # Additional sanity check
        if meta.stored_size > self.page_size * 2:  # Should never be larger than 2x page size
            return
            
        self.ssd_mmap.seek(meta.ssd_offset)
        # Read the full compressed page (header + data)
        full_data = self.ssd_mmap.read(meta.stored_size)
        
        try:
            comp_page = CompressedPage.from_bytes(full_data)
            data = comp_page.decompress(self.page_size)
        except ValueError as e:
            self.corruption_count += 1
            print(f"[Core] Prefetch CRC32 failed for page {page_id}: {e}")
            return
            
        if len(self.ram_cache) >= self.max_ram_cache_pages:
            self._evict_page()

        self.ram_cache[page_id] = data
        self.ram_cache.move_to_end(page_id)
        meta.is_in_ram = True
        meta.compressed_size = 0
        meta.ssd_offset = 0
        meta.update_access()
        
    def _record_page_fault(self):
        """Record page fault for thrashing detection."""
        now = time.perf_counter()
        # Limit window size to prevent memory growth
        if len(self.fault_window) > 1000:
            self.fault_window = self.fault_window[-500:]  # Keep only last 500
        self.fault_window.append((now, 1))
        
        # Remove old entries (older than 1 second)
        self.fault_window = [(t, c) for t, c in self.fault_window if now - t < 1.0]
        
        # Check for thrashing
        recent_faults = sum(c for t, c in self.fault_window)
        if recent_faults > self.thrashing_threshold:
            self.thrashing_detected = True
            self.thrash_events += 1
            # Adaptive response: increase cache size if possible
            if self.adaptive_cache_enabled:
                self._adapt_cache_size()
        else:
            self.thrashing_detected = False
            
    def _adapt_cache_size(self):
        """Adaptively increase cache size when thrashing detected."""
        # Increase cache by 25% if thrashing, but cap at reasonable limit
        old_max = self.max_ram_cache_pages
        new_max = min(int(old_max * 1.25), 10 * 1024 * 1024)  # Cap at 10M pages (~40GB)
        if new_max <= old_max:
            return  # Already at max
        self.max_ram_cache_pages = new_max
        print(f"[Core] Thrashing detected! Increased cache: {old_max} -> {new_max} pages")

    def get_metrics(self) -> dict:
        with self.lock:
            total_access = self.cache_hits + self.cache_misses
            hit_rate = (self.cache_hits / total_access) * 100 if total_access > 0 else 100.0
            compression_ratio = (self.total_uncompressed_bytes / self.total_compressed_bytes) if self.total_compressed_bytes > 0 else 1.0
            ram_latency_ns = 50 
            ssd_latency_ns = 25000 
            effective_latency_ns = (hit_rate/100 * ram_latency_ns) + ((100-hit_rate)/100 * ssd_latency_ns)

            pinned_ram = sum(1 for pid in self.ram_cache if self.page_table.get(pid) and self.page_table[pid].qos_tag in [QoSTag.PHYSICS, QoSTag.STATE])
            
            # Calculate fault rate for thrashing detection
            now = time.perf_counter()
            recent_faults = sum(1 for t, _ in self.fault_window if now - t < 1.0)
            
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
                "qos_traffic": self.qos_traffic,
                "corruption_count": self.corruption_count,
                "thrashing_detected": self.thrashing_detected,
                "thrash_events": self.thrash_events,
                "page_faults_per_sec": recent_faults
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
                # Handle both old tuple format and new dict format
                if isinstance(entry, dict):
                    is_in_ram = entry.get('is_in_ram', False)
                    qos = entry.get('qos_tag', QoSTag.DEFAULT)
                    comp_sz = entry.get('compressed_size', 0)
                    ssd_off = entry.get('ssd_offset', 0)
                    orig_sz = entry.get('original_size', self.page_size)
                    crc = entry.get('crc32', 0)
                else:
                    is_in_ram, qos, comp_sz, ssd_off = entry
                    orig_sz = self.page_size
                    crc = 0
                meta_obj = PageMetadata(pid, qos)
                meta_obj.is_in_ram = is_in_ram
                meta_obj.compressed_size = comp_sz
                meta_obj.ssd_offset = ssd_off
                meta_obj.original_size = orig_sz
                meta_obj.crc32 = crc
                self.page_table[pid] = meta_obj
                self.offset_to_page[ssd_off] = pid
                restored += 1
        return restored

    def close(self):
        with self.lock:
            self.ssd_mmap.close()
            self.pool_file.close()
