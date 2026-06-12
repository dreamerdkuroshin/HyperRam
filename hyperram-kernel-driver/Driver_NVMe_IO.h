// =============================================================================
//  Driver_NVMe_IO.h  —  Shared IOCTL definitions for HyperRAM v2
//
//  Include this in BOTH:
//    • hyperram-kernel-driver/Driver.cpp       (kernel side)
//    • hyperram-daemon/kernel_client.py        (userspace side, values mirrored)
//
//  IOCTL codes use CTL_CODE(FILE_DEVICE_UNKNOWN=0x22, func, METHOD_BUFFERED=0, access)
//  = (0x22 << 16) | (access << 14) | (func << 2) | method
//
//  v2: Added latency tracking fields to HYPERRAM_STATS for paper benchmarks.
// =============================================================================
#pragma once

#ifndef CTL_CODE
#define CTL_CODE(DeviceType,Function,Method,Access) \


    (((DeviceType)<<16)|((Access)<<14)|((Function)<<2)|(Method))
#endif

#define FILE_DEVICE_HYPERRAM        0x00000022  // FILE_DEVICE_UNKNOWN

#ifndef METHOD_BUFFERED
#define METHOD_BUFFERED             0
#endif

#ifndef FILE_READ_ACCESS
#define FILE_READ_ACCESS            0x0001
#endif

#ifndef FILE_WRITE_ACCESS
#define FILE_WRITE_ACCESS           0x0002
#endif

#ifndef FILE_ANY_ACCESS
#define FILE_ANY_ACCESS             0x0000
#endif

// ---------------------------------------------------------------------------
//  IOCTL codes
// ---------------------------------------------------------------------------
#define IOCTL_HYPERRAM_GET_STATS   CTL_CODE(FILE_DEVICE_HYPERRAM, 0x800, METHOD_BUFFERED, FILE_READ_ACCESS)
#define IOCTL_HYPERRAM_FLUSH       CTL_CODE(FILE_DEVICE_HYPERRAM, 0x801, METHOD_BUFFERED, FILE_WRITE_ACCESS)
#define IOCTL_HYPERRAM_RESIZE_POOL CTL_CODE(FILE_DEVICE_HYPERRAM, 0x802, METHOD_BUFFERED, FILE_WRITE_ACCESS)
#define IOCTL_HYPERRAM_READ_PAGE   CTL_CODE(FILE_DEVICE_HYPERRAM, 0x803, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_HYPERRAM_WRITE_PAGE  CTL_CODE(FILE_DEVICE_HYPERRAM, 0x804, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_HYPERRAM_SAVE_METADATA CTL_CODE(FILE_DEVICE_HYPERRAM, 0x805, METHOD_BUFFERED, FILE_WRITE_ACCESS)

// ---------------------------------------------------------------------------
//  Structures
// ---------------------------------------------------------------------------

#pragma pack(push, 8)

// Returned by IOCTL_HYPERRAM_GET_STATS
// v2: Added latency tracking fields for separate RAM-hit vs NVMe-hit measurement
typedef struct _HYPERRAM_STATS {
    ULONG64 TotalReads;          // Total read dispatches
    ULONG64 TotalWrites;         // Total write dispatches
    ULONG64 CacheHits;           // Reads served from kernel RAM cache
    ULONG64 CacheMisses;         // Reads requiring NVMe fetch
    ULONG64 NvmeReads;           // NVMe pool reads
    ULONG64 NvmeWrites;          // NVMe pool writes
    ULONG64 TauUs;               // Current inter-arrival tau in microseconds
    ULONG64 PoolSizeBytes;       // Total NVMe pool size
    ULONG64 PoolUsedBytes;       // Pool bytes currently occupied
    ULONG64 PrefetchesFired;     // Work-item prefetch triggers
    ULONG   StrideConfidence;    // Current stride predictor confidence (0-8)
    LONG    LastStride;          // Detected stride direction
    ULONG   RamCachePages;       // Pages currently in kernel RAM cache
    ULONG   MaxRamCachePages;    // Max RAM cache capacity (pages)
    ULONG   PageSize;            // Bytes per page (always 4096)
    ULONG   _pad;                // alignment
    ULONG64 TotalCompressedBytes;
    ULONG64 TotalUncompressedBytes;
    ULONG64 TotalCompressTimeUs;
    ULONG64 TotalDecompressTimeUs;
} HYPERRAM_STATS, *PHYPERRAM_STATS;

// Input for IOCTL_HYPERRAM_READ_PAGE / WRITE_PAGE
typedef struct _HYPERRAM_PAGE_REQUEST {
    ULONG64 PageId;              // Virtual page ID
    ULONG   QoSTag;              // 0=DEFAULT,1=AI,2=TEXTURE,3=SHADER,4=PHYSICS,5=STATE
    ULONG   DataLengthBytes;     // Must equal PageSize (4096)
    // [followed immediately by DataLengthBytes of page data for WRITE_PAGE]
} HYPERRAM_PAGE_REQUEST, *PHYPERRAM_PAGE_REQUEST;

// Input for IOCTL_HYPERRAM_RESIZE_POOL
typedef struct _HYPERRAM_RESIZE_REQUEST {
    ULONG64 NewPoolSizeGB;       // Target pool size in GB
} HYPERRAM_RESIZE_REQUEST, *PHYPERRAM_RESIZE_REQUEST;

// Persistent pool header - stored at offset 0 of pool file
// Magic: 'HRAM' = 0x4D415248
#define HYPERRAM_POOL_MAGIC 0x4D415248
#define HYPERRAM_POOL_VERSION 1

typedef struct _POOL_HEADER {
    ULONG  Magic;                  // Must equal HYPERRAM_POOL_MAGIC
    ULONG  Version;                // HYPERRAM_POOL_VERSION
    ULONG64 PoolSizeBytes;         // Total pool capacity
    ULONG64 UsedBytes;             // Currently used bytes
    ULONG64 PageTableOffset;       // File offset where page table is stored
    ULONG  PageTableEntries;       // Number of valid page table entries
    ULONG  Checksum;               // CRC32 of header
    ULONG64 Timestamp;             // Last update timestamp (100ns intervals since 1601)
} POOL_HEADER, *PPOOL_HEADER;

// Persistent page table entry - stored after pool header
typedef struct _PERSISTENT_PAGE_ENTRY {
    ULONG64 PageId;
    ULONG   OffsetInSsd;
    ULONG   DataLength;
    BOOLEAN InSsdPool;
    BOOLEAN Reserved[3];
} PERSISTENT_PAGE_ENTRY, *PPERSISTENT_PAGE_ENTRY;

#pragma pack(pop)
