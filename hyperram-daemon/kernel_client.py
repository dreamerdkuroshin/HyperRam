# -*- coding: utf-8 -*-
r"""
=============================================================================
  kernel_client.py  —  Python ↔ HyperRAM Kernel Driver Bridge
=============================================================================
  Opens \\.\HyperRAM (the WDM device created by Driver.cpp) and issues
  DeviceIoControl calls to read/write pages and retrieve live statistics.

  Architecture (Option A — safe hybrid):
    Application code / benchmarks
          │
          ▼
    kernel_client.py  (ctypes DeviceIoControl)
          │
          ▼ \\.\HyperRAM  (Win32 CreateFile)
    HyperRAM.sys  — kernel RAM cache + IRP dispatch
          │  [kernel RAM hit → return immediately]
          │  [kernel RAM miss → IOCTL_READ_PAGE → userspace]
          ▼
    hyperram_service.py  (mmap NVMe pool)
          │
          ▼
    hyperram.pool  (NVMe SSD file, 2–100 GB)

  IOCTL codes mirror Driver_NVMe_IO.h exactly.
=============================================================================
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys
import os
import time

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
GENERIC_READ            = 0x80000000
GENERIC_WRITE           = 0x40000000
OPEN_EXISTING           = 3
FILE_SHARE_READ         = 0x00000001
FILE_SHARE_WRITE        = 0x00000002
INVALID_HANDLE_VALUE    = ctypes.c_void_p(-1).value

# ---------------------------------------------------------------------------
# IOCTL codes  (mirror Driver_NVMe_IO.h CTL_CODE formulas)
# CTL_CODE(DevType, Func, Method, Access)
#   = (DevType<<16) | (Access<<14) | (Func<<2) | Method
# FILE_DEVICE_UNKNOWN = 0x22
# METHOD_BUFFERED     = 0
# FILE_READ_ACCESS    = 1,  FILE_WRITE_ACCESS = 2,  FILE_ANY_ACCESS = 0
# ---------------------------------------------------------------------------
def _ctl(func, access):
    return (0x22 << 16) | (access << 14) | (func << 2) | 0

IOCTL_HYPERRAM_GET_STATS   = _ctl(0x800, 1)   # 0x00226000
IOCTL_HYPERRAM_FLUSH       = _ctl(0x801, 2)   # 0x0022A004
IOCTL_HYPERRAM_RESIZE_POOL = _ctl(0x802, 2)   # 0x0022A008
IOCTL_HYPERRAM_READ_PAGE   = _ctl(0x803, 0)   # 0x0022200C
IOCTL_HYPERRAM_WRITE_PAGE  = _ctl(0x804, 0)   # 0x00222010
IOCTL_HYPERRAM_SAVE_METADATA = _ctl(0x805, 2) # 0x0022A014

PAGE_SIZE = 4096

# ---------------------------------------------------------------------------
# Ctypes structures  (must match Driver_NVMe_IO.h pack(8) layout)
# ---------------------------------------------------------------------------
class HYPERRAM_STATS(ctypes.Structure):
    """Returned by IOCTL_HYPERRAM_GET_STATS."""
    _pack_   = 8
    _fields_ = [
        ("TotalReads",       ctypes.c_uint64),
        ("TotalWrites",      ctypes.c_uint64),
        ("CacheHits",        ctypes.c_uint64),
        ("CacheMisses",      ctypes.c_uint64),
        ("NvmeReads",        ctypes.c_uint64),
        ("NvmeWrites",       ctypes.c_uint64),
        ("TauUs",            ctypes.c_uint64),
        ("PoolSizeBytes",    ctypes.c_uint64),
        ("PoolUsedBytes",    ctypes.c_uint64),
        ("PrefetchesFired",  ctypes.c_uint64),
        ("StrideConfidence", ctypes.c_uint32),
        ("LastStride",       ctypes.c_int32),
        ("RamCachePages",    ctypes.c_uint32),
        ("MaxRamCachePages", ctypes.c_uint32),
        ("PageSize",         ctypes.c_uint32),
        ("_pad",             ctypes.c_uint32),
        ("TotalCompressedBytes", ctypes.c_uint64),
        ("TotalUncompressedBytes", ctypes.c_uint64),
        ("TotalCompressTimeUs", ctypes.c_uint64),
        ("TotalDecompressTimeUs", ctypes.c_uint64),
    ]

    def to_dict(self):
        total = self.CacheHits + self.CacheMisses
        hr = (self.CacheHits / total * 100) if total > 0 else 100.0
        return {
            "total_reads":        self.TotalReads,
            "total_writes":       self.TotalWrites,
            "cache_hits":         self.CacheHits,
            "cache_misses":       self.CacheMisses,
            "hit_rate_pct":       round(hr, 3),
            "nvme_reads":         self.NvmeReads,
            "nvme_writes":        self.NvmeWrites,
            "tau_us":             self.TauUs,
            "pool_size_gb":       self.PoolSizeBytes / (1024**3),
            "pool_used_mb":       self.PoolUsedBytes / (1024**2),
            "prefetches_fired":   self.PrefetchesFired,
            "stride_confidence":  self.StrideConfidence,
            "last_stride":        self.LastStride,
            "ram_cache_pages":    self.RamCachePages,
            "max_ram_pages":      self.MaxRamCachePages,
            "page_size":          self.PageSize,
            "compressed_bytes":   self.TotalCompressedBytes,
            "uncompressed_bytes": self.TotalUncompressedBytes,
            "compress_time_us":   self.TotalCompressTimeUs,
            "decompress_time_us": self.TotalDecompressTimeUs,
        }

    def __str__(self):
        d = self.to_dict()
        lines = [
            "  ── HyperRAM Kernel Stats ─────────────────────────────",
            f"  Hit rate          : {d['hit_rate_pct']:.3f}%",
            f"  Total reads       : {d['total_reads']}",
            f"  Total writes      : {d['total_writes']}",
            f"  Cache hits        : {d['cache_hits']}",
            f"  Cache misses      : {d['cache_misses']}",
            f"  NVMe reads        : {d['nvme_reads']}",
            f"  NVMe writes       : {d['nvme_writes']}",
            f"  Tau (µs)          : {d['tau_us']}",
            f"  Stride confidence : {d['stride_confidence']}/8  stride={d['last_stride']}",
            f"  RAM cache         : {d['ram_cache_pages']} / {d['max_ram_pages']} pages",
            f"  Prefetches fired  : {d['prefetches_fired']}",
            f"  Pool used         : {d['pool_used_mb']:.2f} MB",
            f"  Comp ratio        : {(d['uncompressed_bytes'] / d['compressed_bytes']) if d['compressed_bytes'] > 0 else 1.0:.2f}x",
            f"  Comp CPU time     : {d['compress_time_us']} µs",
            f"  Decomp CPU time   : {d['decompress_time_us']} µs",
            "  ────────────────────────────────────────────────────",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Win32 DeviceIoControl via ctypes
# ---------------------------------------------------------------------------
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

_k32.CreateFileW.restype  = ctypes.c_void_p
_k32.CreateFileW.argtypes = [
    wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
    wt.DWORD, wt.DWORD, ctypes.c_void_p
]

_k32.DeviceIoControl.restype  = wt.BOOL
_k32.DeviceIoControl.argtypes = [
    ctypes.c_void_p, wt.DWORD,
    ctypes.c_void_p, wt.DWORD,
    ctypes.c_void_p, wt.DWORD,
    ctypes.POINTER(wt.DWORD), ctypes.c_void_p
]

_k32.CloseHandle.restype  = wt.BOOL
_k32.CloseHandle.argtypes = [ctypes.c_void_p]


class HyperRAMKernelClient:
    r"""
    Userspace client for the HyperRAM kernel driver.

    If the driver is not loaded (\\.\HyperRAM not present), this client
    falls back to a pure-userspace simulation using core.HyperRAMEngine
    so benchmarks can run without requiring Test Signing Mode.
    """

    DEVICE_PATH = r"\\.\HyperRAM"

    def __init__(self, fallback_engine=None):
        """
        Args:
            fallback_engine: HyperRAMEngine instance to use if driver absent.
        """
        self._handle   = None
        self._fallback = fallback_engine
        self._kernel   = False
        self._open()

    def _open(self):
        handle = _k32.CreateFileW(
            self.DEVICE_PATH,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None
        )
        if handle == INVALID_HANDLE_VALUE or handle is None:
            err = ctypes.get_last_error()
            print(f"  [kernel_client] Driver not found (error {err}). "
                  f"Using userspace fallback.")
            self._kernel = False
        else:
            self._handle  = handle
            self._kernel  = True
            print(f"  [kernel_client] Connected to HyperRAM kernel driver at {self.DEVICE_PATH}")

    # ---- IOCTL helper --------------------------------------------------------
    def _ioctl(self, code, in_buf=None, out_size=0):
        """Issue a DeviceIoControl. Returns output bytes or None on error."""
        if not self._kernel:
            return None

        in_ptr  = ctypes.cast(in_buf,  ctypes.c_void_p) if in_buf  else None
        in_sz   = len(in_buf) if in_buf else 0
        out_buf = (ctypes.c_ubyte * out_size)() if out_size > 0 else None
        out_ptr = ctypes.cast(out_buf, ctypes.c_void_p) if out_buf else None
        returned = wt.DWORD(0)

        ok = _k32.DeviceIoControl(
            self._handle, code,
            in_ptr,  in_sz,
            out_ptr, out_size,
            ctypes.byref(returned), None
        )
        if not ok:
            err = ctypes.get_last_error()
            print(f"  [kernel_client] IOCTL 0x{code:08X} failed: error {err}")
            return None
        return bytes(out_buf[:returned.value]) if out_buf else b""

    # ---- Public API ----------------------------------------------------------
    def get_stats(self):
        """
        Returns:
            HYPERRAM_STATS struct (kernel) or a dict (fallback).
        """
        if self._kernel:
            raw = self._ioctl(IOCTL_HYPERRAM_GET_STATS,
                              out_size=ctypes.sizeof(HYPERRAM_STATS))
            if raw and len(raw) >= ctypes.sizeof(HYPERRAM_STATS):
                stats = HYPERRAM_STATS.from_buffer_copy(raw)
                return stats
            return None
        elif self._fallback:
            return self._fallback.get_metrics()
        return None

    def read_page(self, page_id: int) -> bytes:
        """Read a 4096-byte page. Uses kernel driver or fallback engine."""
        if self._kernel:
            req = struct.pack("<QII", page_id, 0, PAGE_SIZE)  # PageId, QoS, DataLen
            raw = self._ioctl(IOCTL_HYPERRAM_READ_PAGE,
                              in_buf=req, out_size=PAGE_SIZE)
            return raw if (raw and len(raw) == PAGE_SIZE) else b'\x00' * PAGE_SIZE
        elif self._fallback:
            return self._fallback.read_page(page_id)
        return b'\x00' * PAGE_SIZE

    def write_page(self, page_id: int, data: bytes, qos: int = 0):
        """Write a 4096-byte page. Uses kernel driver or fallback engine."""
        if len(data) != PAGE_SIZE:
            data = data.ljust(PAGE_SIZE, b'\x00')[:PAGE_SIZE]
        if self._kernel:
            req = struct.pack("<QII", page_id, qos, PAGE_SIZE) + data
            self._ioctl(IOCTL_HYPERRAM_WRITE_PAGE, in_buf=req)
        elif self._fallback:
            self._fallback.write_page(page_id, data)

    def flush(self):
        """Flush all dirty pages from RAM cache → NVMe pool."""
        if self._kernel:
            self._ioctl(IOCTL_HYPERRAM_FLUSH)

    def save_metadata(self):
        """Explicitly save metadata to pool file for persistent restart."""
        if self._kernel:
            self._ioctl(IOCTL_HYPERRAM_SAVE_METADATA)

    def resize_pool(self, new_gb: int):
        """Request the driver to resize the NVMe pool."""
        if self._kernel:
            req = struct.pack("<Q", new_gb)
            self._ioctl(IOCTL_HYPERRAM_RESIZE_POOL, in_buf=req)
        elif self._fallback:
            self._fallback.resize_pool(new_gb)

    @property
    def is_kernel_mode(self):
        return self._kernel

    def close(self):
        if self._handle:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
def smoke_test():
    print("\n" + "="*60)
    print("  HyperRAM Kernel Client — Smoke Test")
    print("="*60)

    # Try kernel first, fall back to Python engine
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        from core import HyperRAMEngine
        pool = os.path.join(os.path.dirname(__file__), "..", "hyperram.pool")
        engine = HyperRAMEngine(ssd_pool_path=os.path.abspath(pool))
        engine.max_ram_cache_pages = 256
        fallback = engine
    except Exception as e:
        print(f"  Warning: could not load fallback engine: {e}")
        fallback = None

    client = HyperRAMKernelClient(fallback_engine=fallback)
    print(f"  Mode: {'KERNEL' if client.is_kernel_mode else 'USERSPACE FALLBACK'}")

    # Write 5 pages
    print("\n  Writing 5 test pages...")
    for i in range(5):
        data = bytes([i * 51]) * PAGE_SIZE
        client.write_page(i, data)
        print(f"    Page {i}: wrote pattern 0x{i*51:02X}")

    # Read back
    print("\n  Reading back 5 pages...")
    ok = True
    for i in range(5):
        data = client.read_page(i)
        expected = bytes([i * 51]) * PAGE_SIZE
        match = data == expected
        if not match:
            ok = False
        print(f"    Page {i}: {'✓ MATCH' if match else '✗ MISMATCH'}")

    # Stats
    print("\n  Fetching driver stats...")
    stats = client.get_stats()
    if stats:
        if hasattr(stats, 'to_dict'):
            print(stats)
        else:
            print(f"    Metrics: {stats}")

    client.close()
    if fallback:
        fallback.close()

    print(f"\n  Result: {'PASS' if ok else 'FAIL'}")
    print("="*60 + "\n")
    return ok


if __name__ == "__main__":
    smoke_test()
