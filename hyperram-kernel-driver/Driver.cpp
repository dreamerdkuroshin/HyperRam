#pragma warning(disable: 4996) // Suppress ExAllocatePoolWithTag deprecation
#include <ntifs.h>
#include "Driver_NVMe_IO.h"

// --------------------------------------------------------------------------
// HYPERRAM KERNEL-MODE DRIVER  (Pure WDM - Zero WDF/KMDF dependency)
//
// ROOT-CAUSE FIX: The previous WDF build required WDFLDR to locate and bind
// Wdf01035.sys at load time.  On this machine that service is not registered,
// so WdfDriverCreate returned STATUS_INVALID_PARAMETER (Win32 Error 87) before
// DriverEntry could execute a single line of code.
//
// Pure WDM links directly to ntoskrnl.lib / wdm.lib -- no framework loader,
// no KmdfLibraryVersion key, no co-installer.
// --------------------------------------------------------------------------

// ---- Build switches -------------------------------------------------------
// Define HYPERRAM_VERBOSE_LOG to enable per-I/O ZwCreateFile logging.
// WARNING: Each WriteLog() call issues ZwCreateFile+ZwWriteFile+ZwClose
// (~5-50 µs each). Leave UNDEFINED during any latency benchmark run.
// #define HYPERRAM_VERBOSE_LOG

// ---- Constants ------------------------------------------------------------
#ifndef PAGE_SIZE
#define PAGE_SIZE      4096
#endif
#define SSD_PAGE_SIZE       PAGE_SIZE                       // 100% - real compression target
#define SSD_POOL_SIZE       (16 * 1024 * 1024)             // 16 MB simulated SSD
#define RAM_CACHE_SIZE      ( 4 * 1024 * 1024)             //  4 MB hot RAM cache
#define MAX_SSD_PAGES       (SSD_POOL_SIZE / SSD_PAGE_SIZE) // 8 192 slots
#define MAX_RAM_CACHE_PAGES (RAM_CACHE_SIZE / PAGE_SIZE)   // 1 024 pages

// FIX C1: The page table uses slot = pageId % MAX_SSD_PAGES (open addressing).
// Two page IDs that differ by MAX_SSD_PAGES map to the same slot.
// We detect collisions by comparing the stored PageId; a mismatch = eviction.
// For correctness, a working set larger than MAX_SSD_PAGES unique pages will
// experience silent evictions -- this is documented behaviour for the prototype.

#define DEVICE_NAME  L"\\Device\\HyperRAM"
#define SYMLINK_NAME L"\\DosDevices\\HyperRAM"
#define POOL_TAG     'HRAM'

// ---- Data structures ------------------------------------------------------
typedef struct _PAGE_ENTRY {
    ULONG64 PageId;
    BOOLEAN InRamCache;
    BOOLEAN InSsdPool;
    ULONG   OffsetInSsd;
    ULONG   DataLength;
} PAGE_ENTRY, *PPAGE_ENTRY;

typedef struct _DRIVER_CONTEXT {
    HANDLE         PoolFileHandle;  // Real SSD pool file handle
    PVOID          RamCacheBuffer;
    PPAGE_ENTRY    PageTable;
    KSPIN_LOCK     Lock;
    ULONG64        PrefetchPageId;
    LONG           PrefetchStride;
    LONG           PrefetchDepth;
    PIO_WORKITEM   PrefetchWorkItem;
    PVOID          Workspace;
    PDEVICE_OBJECT DeviceObject;
    BOOLEAN        SymlinkCreated;

    // Tau-based predictor state
    LARGE_INTEGER  LastAccessTime;
    BOOLEAN        HasLastAccess;
    LONGLONG       InterArrivalTauUs; // Tau in microseconds
    ULONG64        LastPageId;
    LONG           LastStride;
    LONG           StrideConfidence;
    // FIX Bug-6: Guard against double-queuing the same work item
    BOOLEAN        PrefetchPending;

    // RAM cache clock-hand for true LRU-approximate eviction
    // RamClockHand cycles 0..MAX_RAM_CACHE_PAGES-1 and picks victim slots.
    ULONG          RamClockHand;

    // BUG1 ROOT-CAUSE FIX: RamSlotOwner[i] = the pageId currently stored in
    // physical RAM slot i, or (ULONG64)-1 if empty.  This is the single source
    // of truth for RAM-slot ownership.  PageTable[ssdSlot].InRamCache is a
    // cached flag derived from this; it must be cleared whenever the slot is
    // taken by a different page.  Without this array, 8 different SSD slots
    // (that all hash to the same RAM slot via slot%1024) each see
    // PageTable[ssdSlot].InRamCache==FALSE and all increment the counter.
    ULONG64        RamSlotOwner[MAX_RAM_CACHE_PAGES];

    // Stats counters
    volatile ULONG64 TotalReads;
    volatile ULONG64 TotalWrites;
    volatile ULONG64 CacheHits;
    volatile ULONG64 CacheMisses;
    volatile ULONG64 NvmeReads;
    volatile ULONG64 NvmeWrites;
    volatile ULONG64 PrefetchesFired;
    volatile ULONG64 PoolUsedBytes;
    volatile ULONG   RamCachePages;

    // I/O timing for compression/decompression accounting (Bug 2 fix)
    volatile ULONG64 TotalCompressTimeUs;    // µs spent in write I/O path
    volatile ULONG64 TotalDecompressTimeUs;  // µs spent in read I/O path
    volatile ULONG64 TotalCompressedBytes;   // bytes written to pool file
    volatile ULONG64 TotalUncompressedBytes; // logical bytes written
} DRIVER_CONTEXT, *PDRIVER_CONTEXT;

static PDRIVER_CONTEXT g_Context = NULL;

// ---- Forward declarations -------------------------------------------------
extern "C" DRIVER_INITIALIZE DriverEntry;
DRIVER_UNLOAD HyperRAM_Unload;
__drv_dispatchType(IRP_MJ_CREATE)
__drv_dispatchType(IRP_MJ_CLOSE)
DRIVER_DISPATCH HyperRAM_CreateClose;
__drv_dispatchType(IRP_MJ_READ)
DRIVER_DISPATCH HyperRAM_Read;
__drv_dispatchType(IRP_MJ_WRITE)
DRIVER_DISPATCH HyperRAM_Write;
__drv_dispatchType(IRP_MJ_DEVICE_CONTROL)
DRIVER_DISPATCH HyperRAM_DeviceControl;
IO_WORKITEM_ROUTINE HyperRAM_PrefetchWorkItem;

// ---- Log helper -----------------------------------------------------------
// WriteLog() requires PASSIVE_LEVEL (file I/O). It is safe to call after
// releasing any spin locks, but NEVER inside a spin lock critical section.
// Gated by HYPERRAM_VERBOSE_LOG; disable for benchmarking (see top of file).
static VOID WriteLog(const char* msg)
{
#ifdef HYPERRAM_VERBOSE_LOG
    UNICODE_STRING path;
    OBJECT_ATTRIBUTES oa;
    RtlInitUnicodeString(&path, L"\\SystemRoot\\Temp\\hyperram.log");
    InitializeObjectAttributes(&oa, &path,
        OBJ_CASE_INSENSITIVE | OBJ_KERNEL_HANDLE, NULL, NULL);

    HANDLE h;
    IO_STATUS_BLOCK isb;
    NTSTATUS s = ZwCreateFile(&h,
        FILE_APPEND_DATA | SYNCHRONIZE, &oa, &isb, NULL,
        FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ,
        FILE_OPEN_IF, FILE_SYNCHRONOUS_IO_NONALERT, NULL, 0);
    if (NT_SUCCESS(s)) {
        ULONG len = 0;
        while (msg[len]) len++;
        ZwWriteFile(h, NULL, NULL, NULL, &isb, (PVOID)msg, len, NULL, NULL);
        ZwClose(h);
    }
#else
    // DbgPrint is safe at any IRQL and has negligible overhead.
    DbgPrintEx(DPFLTR_DEFAULT_ID, DPFLTR_TRACE_LEVEL, "HyperRAM: %s", msg);
    UNREFERENCED_PARAMETER(msg);
#endif
}

// ---- Persistent Metadata Helper -------------------------------------------
// SavePageTableMetadata() - Saves page table state to pool file header
// Called periodically and on driver unload for fast restart recovery
static VOID SavePageTableMetadata()
{
    if (!g_Context || !g_Context->PoolFileHandle) return;
    
    // Build pool header
    POOL_HEADER header;
    RtlZeroMemory(&header, sizeof(POOL_HEADER));
    header.Magic = HYPERRAM_POOL_MAGIC;
    header.Version = HYPERRAM_POOL_VERSION;
    header.PoolSizeBytes = SSD_POOL_SIZE;
    
    // Count valid entries
    ULONG validEntries = 0;
    for (ULONG i = 0; i < MAX_SSD_PAGES; i++) {
        if (g_Context->PageTable[i].InSsdPool && 
            g_Context->PageTable[i].PageId != (ULONG64)-1) {
            validEntries++;
        }
    }
    
    header.PageTableEntries = validEntries;
    header.UsedBytes = g_Context->PoolUsedBytes;
    header.PageTableOffset = sizeof(POOL_HEADER); // Store right after header
    
    // Get timestamp
    LARGE_INTEGER now;
    KeQuerySystemTime(&now);
    header.Timestamp = now.QuadPart;
    
    // Calculate checksum
    ULONG checksum = 0;
    PUCHAR headerBytes = (PUCHAR)&header;
    for (ULONG i = 0; i < sizeof(POOL_HEADER); i++) {
        if (i != offsetof(POOL_HEADER, Checksum)) {
            checksum ^= ((ULONG)headerBytes[i]) << ((i % 4) * 8);
        }
    }
    header.Checksum = checksum;
    
    // Write header
    IO_STATUS_BLOCK isb;
    LARGE_INTEGER offset;
    offset.QuadPart = 0;
    
    NTSTATUS status = ZwWriteFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
        &isb, &header, sizeof(POOL_HEADER), &offset, NULL);
    
    if (!NT_SUCCESS(status)) {
        WriteLog("[HyperRAM] Failed to write pool header.\r\n");
        return;
    }
    
    // Build and write persistent page table
    if (validEntries > 0) {
        PPERSISTENT_PAGE_ENTRY persistTable = (PPERSISTENT_PAGE_ENTRY)ExAllocatePoolWithTag(
            NonPagedPoolNx, sizeof(PERSISTENT_PAGE_ENTRY) * validEntries, POOL_TAG);
        
        if (persistTable) {
            ULONG idx = 0;
            for (ULONG i = 0; i < MAX_SSD_PAGES; i++) {
                if (g_Context->PageTable[i].InSsdPool && 
                    g_Context->PageTable[i].PageId != (ULONG64)-1) {
                    persistTable[idx].PageId = g_Context->PageTable[i].PageId;
                    persistTable[idx].OffsetInSsd = g_Context->PageTable[i].OffsetInSsd;
                    persistTable[idx].DataLength = g_Context->PageTable[i].DataLength;
                    persistTable[idx].InSsdPool = TRUE;
                    idx++;
                }
            }
            
            offset.QuadPart = sizeof(POOL_HEADER);
            status = ZwWriteFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                &isb, persistTable, sizeof(PERSISTENT_PAGE_ENTRY) * validEntries, &offset, NULL);
            
            if (NT_SUCCESS(status)) {
                WriteLog("[HyperRAM] Persistent metadata saved.\r\n");
            } else {
                WriteLog("[HyperRAM] Failed to write persistent page table.\r\n");
            }
            
            ExFreePoolWithTag(persistTable, POOL_TAG);
        }
    }
}

// --------------------------------------------------------------------------
// 1.  DriverEntry
// --------------------------------------------------------------------------
extern "C" NTSTATUS NTAPI DriverEntry(
    _In_ PDRIVER_OBJECT  DriverObject,
    _In_ PUNICODE_STRING RegistryPath)
{
    UNREFERENCED_PARAMETER(RegistryPath);
    NTSTATUS       status = STATUS_INSUFFICIENT_RESOURCES;
    UNICODE_STRING devName, symName;

    WriteLog("----------------------------------------\r\n");
    WriteLog("[HyperRAM] DriverEntry entered (WDM).\r\n");

    // --- Dispatch table ---
    DriverObject->DriverUnload = HyperRAM_Unload;
    for (ULONG i = 0; i <= IRP_MJ_MAXIMUM_FUNCTION; i++)
        DriverObject->MajorFunction[i] = HyperRAM_CreateClose;
    DriverObject->MajorFunction[IRP_MJ_READ]           = HyperRAM_Read;
    DriverObject->MajorFunction[IRP_MJ_WRITE]          = HyperRAM_Write;
    DriverObject->MajorFunction[IRP_MJ_DEVICE_CONTROL] = HyperRAM_DeviceControl;

    // --- Allocate global context ---
    g_Context = (PDRIVER_CONTEXT)ExAllocatePoolWithTag(
        NonPagedPoolNx, sizeof(DRIVER_CONTEXT), POOL_TAG);
    if (!g_Context) {
        WriteLog("[HyperRAM] Context alloc failed!\r\n");
        return STATUS_INSUFFICIENT_RESOURCES;
    }
    RtlZeroMemory(g_Context, sizeof(DRIVER_CONTEXT));
    g_Context->InterArrivalTauUs = 10000; // 10ms default
    g_Context->LastStride = 1;

    // --- Allocate RAM / PageTable buffers ---
    g_Context->RamCacheBuffer = ExAllocatePoolWithTag(NonPagedPoolNx, RAM_CACHE_SIZE, POOL_TAG);
    g_Context->PageTable      = (PPAGE_ENTRY)ExAllocatePoolWithTag(
        NonPagedPoolNx, sizeof(PAGE_ENTRY) * MAX_SSD_PAGES, POOL_TAG);

    if (!g_Context->RamCacheBuffer || !g_Context->PageTable) {
        WriteLog("[HyperRAM] Buffer alloc failed!\r\n");
        goto Cleanup;
    }

    // --- Allocate compression workspace ---
    ULONG workspace_size = 0;
    NTSTATUS workspace_status = RtlGetCompressionWorkSpaceSize(COMPRESSION_FORMAT_XPRESS, &workspace_size, NULL);
    if (!NT_SUCCESS(workspace_status)) {
        WriteLog("[HyperRAM] Failed to get compression workspace size!\r\n");
        status = workspace_status;
        goto Cleanup;
    }
    g_Context->Workspace = ExAllocatePoolWithTag(NonPagedPoolNx, workspace_size, POOL_TAG);
    if (!g_Context->Workspace) {
        WriteLog("[HyperRAM] Compression workspace alloc failed!\r\n");
        status = STATUS_INSUFFICIENT_RESOURCES;
        goto Cleanup;
    }

    RtlZeroMemory(g_Context->RamCacheBuffer, RAM_CACHE_SIZE);
    RtlZeroMemory(g_Context->PageTable, sizeof(PAGE_ENTRY) * MAX_SSD_PAGES);
    for (ULONG i = 0; i < MAX_SSD_PAGES; i++)
        g_Context->PageTable[i].PageId = (ULONG64)-1;
    // BUG1 FIX: initialise RAM slot ownership table
    for (ULONG i = 0; i < MAX_RAM_CACHE_PAGES; i++)
        g_Context->RamSlotOwner[i] = (ULONG64)-1;

    // --- Open/Create Real SSD Pool File ---
    UNICODE_STRING poolPath;
    OBJECT_ATTRIBUTES poolOa;
    IO_STATUS_BLOCK poolIsb;
    RtlInitUnicodeString(&poolPath, L"\\??\\C:\\hyperram.pool");
    InitializeObjectAttributes(&poolOa, &poolPath,
        OBJ_CASE_INSENSITIVE | OBJ_KERNEL_HANDLE, NULL, NULL);

    status = ZwCreateFile(&g_Context->PoolFileHandle,
        GENERIC_READ | GENERIC_WRITE | SYNCHRONIZE,
        &poolOa, &poolIsb, NULL,
        FILE_ATTRIBUTE_NORMAL,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        FILE_OPEN_IF,
        FILE_SYNCHRONOUS_IO_NONALERT,
        NULL, 0);

    if (!NT_SUCCESS(status)) {
        WriteLog("[HyperRAM] Failed to open/create SSD pool file C:\\hyperram.pool!\r\n");
        goto Cleanup;
    }

    // Set end-of-file size to pool capacity if new or smaller
    FILE_END_OF_FILE_INFORMATION eofInfo;
    eofInfo.EndOfFile.QuadPart = SSD_POOL_SIZE;
    status = ZwSetInformationFile(g_Context->PoolFileHandle, &poolIsb, &eofInfo, sizeof(eofInfo), FileEndOfFileInformation);
    if (!NT_SUCCESS(status)) {
        WriteLog("[HyperRAM] Warning: failed to set pool file EOF size.\r\n");
    }

    // --- PERSISTENT METADATA: Try to restore page table from pool header ---
    POOL_HEADER poolHeader;
    IO_STATUS_BLOCK readIsb;
    LARGE_INTEGER headerOffset;
    headerOffset.QuadPart = 0;
    
    status = ZwReadFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
        &readIsb, &poolHeader, sizeof(POOL_HEADER), &headerOffset, NULL);
    
    BOOLEAN restored = FALSE;
    if (NT_SUCCESS(status) && 
        readIsb.Information == sizeof(POOL_HEADER) &&
        poolHeader.Magic == HYPERRAM_POOL_MAGIC &&
        poolHeader.Version == HYPERRAM_POOL_VERSION) {
        
        // Validate header checksum (simple XOR-based for now)
        ULONG computedChecksum = 0;
        PUCHAR headerBytes = (PUCHAR)&poolHeader;
        for (ULONG i = 0; i < sizeof(POOL_HEADER); i++) {
            if (i != offsetof(POOL_HEADER, Checksum)) {
                computedChecksum ^= ((ULONG)headerBytes[i]) << ((i % 4) * 8);
            }
        }
        
        if (computedChecksum == poolHeader.Checksum &&
            poolHeader.PageTableEntries <= MAX_SSD_PAGES) {
            
            // Read persistent page table
            PPERSISTENT_PAGE_ENTRY persistTable = (PPERSISTENT_PAGE_ENTRY)ExAllocatePoolWithTag(
                NonPagedPoolNx, sizeof(PERSISTENT_PAGE_ENTRY) * poolHeader.PageTableEntries, POOL_TAG);
            
            if (persistTable) {
                LARGE_INTEGER ptOffset;
                ptOffset.QuadPart = poolHeader.PageTableOffset;
                
                status = ZwReadFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                    &readIsb, persistTable, 
                    sizeof(PERSISTENT_PAGE_ENTRY) * poolHeader.PageTableEntries,
                    &ptOffset, NULL);
                
                if (NT_SUCCESS(status)) {
                    // Restore page table
                    for (ULONG i = 0; i < poolHeader.PageTableEntries; i++) {
                        if (persistTable[i].InSsdPool && persistTable[i].PageId != (ULONG64)-1) {
                            ULONG slot = (ULONG)(persistTable[i].PageId % MAX_SSD_PAGES);
                            
                            // Linear probe to find slot
                            for (ULONG j = 0; j < MAX_SSD_PAGES; j++) {
                                if (g_Context->PageTable[slot].PageId == (ULONG64)-1 ||
                                    g_Context->PageTable[slot].PageId == persistTable[i].PageId) {
                                    break;
                                }
                                slot = (slot + 1) % MAX_SSD_PAGES;
                            }
                            
                            g_Context->PageTable[slot].PageId = persistTable[i].PageId;
                            g_Context->PageTable[slot].InSsdPool = TRUE;
                            g_Context->PageTable[slot].OffsetInSsd = persistTable[i].OffsetInSsd;
                            g_Context->PageTable[slot].DataLength = persistTable[i].DataLength;
                            g_Context->PageTable[slot].InRamCache = FALSE;
                        }
                    }
                    
                    g_Context->PoolUsedBytes = poolHeader.UsedBytes;
                    restored = TRUE;
                    WriteLog("[HyperRAM] Persistent metadata restored successfully.\r\n");
                }
                
                ExFreePoolWithTag(persistTable, POOL_TAG);
            }
        }
    }
    
    if (!restored) {
        WriteLog("[HyperRAM] No valid persistent metadata found, starting fresh.\r\n");
    }

    // --- Spin lock ---
    KeInitializeSpinLock(&g_Context->Lock);

    // --- Create control device ---
    RtlInitUnicodeString(&devName, DEVICE_NAME);
    status = IoCreateDevice(
        DriverObject,
        0,
        &devName,
        FILE_DEVICE_UNKNOWN,
        FILE_DEVICE_SECURE_OPEN,
        FALSE,
        &g_Context->DeviceObject);
    if (!NT_SUCCESS(status)) {
        WriteLog("[HyperRAM] IoCreateDevice failed!\r\n");
        goto Cleanup;
    }
    g_Context->DeviceObject->Flags |= DO_BUFFERED_IO;
    g_Context->DeviceObject->Flags &= ~DO_DEVICE_INITIALIZING;

    // --- Symbolic link ---
    RtlInitUnicodeString(&symName, SYMLINK_NAME);
    status = IoCreateSymbolicLink(&symName, &devName);
    if (!NT_SUCCESS(status)) {
        WriteLog("[HyperRAM] IoCreateSymbolicLink failed!\r\n");
        IoDeleteDevice(g_Context->DeviceObject);
        g_Context->DeviceObject = NULL;
        goto Cleanup;
    }
    g_Context->SymlinkCreated = TRUE;

    // --- Work item for async prefetcher ---
    g_Context->PrefetchWorkItem = IoAllocateWorkItem(g_Context->DeviceObject);
    if (!g_Context->PrefetchWorkItem) {
        WriteLog("[HyperRAM] IoAllocateWorkItem failed!\r\n");
        status = STATUS_INSUFFICIENT_RESOURCES;
        IoDeleteSymbolicLink(&symName);
        g_Context->SymlinkCreated = FALSE;
        IoDeleteDevice(g_Context->DeviceObject);
        g_Context->DeviceObject = NULL;
        goto Cleanup;
    }

    WriteLog("[HyperRAM] Driver started successfully. Control device ready.\r\n");
    return STATUS_SUCCESS;

    Cleanup:
    if (g_Context) {
        if (g_Context->PoolFileHandle) ZwClose(g_Context->PoolFileHandle);
        if (g_Context->RamCacheBuffer) ExFreePoolWithTag(g_Context->RamCacheBuffer, POOL_TAG);
        if (g_Context->PageTable)      ExFreePoolWithTag(g_Context->PageTable,      POOL_TAG);
        if (g_Context->Workspace)      ExFreePoolWithTag(g_Context->Workspace,      POOL_TAG);
        ExFreePoolWithTag(g_Context, POOL_TAG);
        g_Context = NULL;
    }
    return status ? status : STATUS_INSUFFICIENT_RESOURCES;
}

// --------------------------------------------------------------------------
// 2.  DriverUnload
// --------------------------------------------------------------------------
VOID HyperRAM_Unload(_In_ PDRIVER_OBJECT DriverObject)
{
    UNREFERENCED_PARAMETER(DriverObject);

    if (!g_Context) return;

    // PERSISTENT METADATA: Save page table state before unloading
    SavePageTableMetadata();

    if (g_Context->PrefetchWorkItem) {
        IoFreeWorkItem(g_Context->PrefetchWorkItem);
        g_Context->PrefetchWorkItem = NULL;
    }
    if (g_Context->SymlinkCreated) {
        UNICODE_STRING symName;
        RtlInitUnicodeString(&symName, SYMLINK_NAME);
        IoDeleteSymbolicLink(&symName);
    }
    if (g_Context->DeviceObject)
        IoDeleteDevice(g_Context->DeviceObject);

    if (g_Context->PoolFileHandle) {
        ZwClose(g_Context->PoolFileHandle);
        g_Context->PoolFileHandle = NULL;
    }
    if (g_Context->RamCacheBuffer) ExFreePoolWithTag(g_Context->RamCacheBuffer, POOL_TAG);
    if (g_Context->PageTable)      ExFreePoolWithTag(g_Context->PageTable,      POOL_TAG);
    if (g_Context->Workspace)      ExFreePoolWithTag(g_Context->Workspace,      POOL_TAG);

    ExFreePoolWithTag(g_Context, POOL_TAG);
    g_Context = NULL;

    WriteLog("[HyperRAM] Driver unloaded.\r\n");
}

// --------------------------------------------------------------------------
// 3.  Create / Close  (open/close handles — just succeed)
// --------------------------------------------------------------------------
NTSTATUS HyperRAM_CreateClose(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_ PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}

// --------------------------------------------------------------------------
// 4.  Prefetch Work Item  (PASSIVE_LEVEL — safe to call WriteLog)
// --------------------------------------------------------------------------
VOID HyperRAM_PrefetchWorkItem(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_opt_ PVOID Context)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    UNREFERENCED_PARAMETER(Context);

    if (!g_Context) return;

    KIRQL oldIrql;
    KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
    // FIX Bug-6: Clear the pending flag so new prefetches can be queued
    g_Context->PrefetchPending = FALSE;
    ULONG64 prefetchBaseId = g_Context->PrefetchPageId;
    LONG    stride         = g_Context->PrefetchStride;
    LONG    depth          = g_Context->PrefetchDepth;
    KeReleaseSpinLock(&g_Context->Lock, oldIrql);

    if (depth <= 0) return;

    for (LONG d = 1; d <= depth; d++) {
        ULONG64 targetPageId = prefetchBaseId + d * stride;
        ULONG slot = (ULONG)(targetPageId % MAX_SSD_PAGES);
        ULONG offset = 0;
        BOOLEAN shouldPrefetch = FALSE;

        KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
        // FIX C1: Only prefetch if the slot actually holds our target page.
        // FIX C4: Only promote if RAM cache is below capacity.
        if (g_Context->PageTable[slot].PageId   == targetPageId &&
            g_Context->PageTable[slot].InSsdPool                &&
           !g_Context->PageTable[slot].InRamCache               &&
            g_Context->RamCachePages < MAX_RAM_CACHE_PAGES) {
            
            offset = g_Context->PageTable[slot].OffsetInSsd;
            shouldPrefetch = TRUE;
        }
        KeReleaseSpinLock(&g_Context->Lock, oldIrql);

        if (shouldPrefetch) {
            // BUG1 FIX: RAM slot derived from targetPageId, not SSD slot
            ULONG ramSlot = (ULONG)(targetPageId % MAX_RAM_CACHE_PAGES);
            PUCHAR dst = (PUCHAR)g_Context->RamCacheBuffer + (ULONG64)ramSlot * PAGE_SIZE;

            IO_STATUS_BLOCK ioStatus;
            LARGE_INTEGER byteOffset;
            byteOffset.QuadPart = (LONGLONG)offset;

            NTSTATUS status = ZwReadFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                                         &ioStatus, dst, SSD_PAGE_SIZE, &byteOffset, NULL);

            if (NT_SUCCESS(status)) {
                RtlZeroMemory(dst + SSD_PAGE_SIZE, PAGE_SIZE - SSD_PAGE_SIZE);

                KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
                // Re-verify page still belongs to this slot
                if (g_Context->PageTable[slot].PageId == targetPageId &&
                    !g_Context->PageTable[slot].InRamCache) {

                    // BUG1 FIX: evict whoever currently owns ramSlot
                    ULONG64 oldOwner = g_Context->RamSlotOwner[ramSlot];
                    if (oldOwner != (ULONG64)-1 && oldOwner != targetPageId) {
                        ULONG oldSsdSlot = (ULONG)(oldOwner % MAX_SSD_PAGES);
                        // Linear-probe to find the old owner's actual SSD slot
                        for (ULONG oi = 0; oi < MAX_SSD_PAGES; oi++) {
                            ULONG chk = (oldSsdSlot + oi) % MAX_SSD_PAGES;
                            if (g_Context->PageTable[chk].PageId == oldOwner) {
                                if (g_Context->PageTable[chk].InRamCache) {
                                    g_Context->PageTable[chk].InRamCache = FALSE;
                                    if (g_Context->RamCachePages > 0) g_Context->RamCachePages--;
                                }
                                break;
                            }
                            if (g_Context->PageTable[chk].PageId == (ULONG64)-1) break;
                        }
                    }

                    if (g_Context->RamCachePages < MAX_RAM_CACHE_PAGES ||
                        g_Context->RamSlotOwner[ramSlot] != (ULONG64)-1) {
                        g_Context->PageTable[slot].InRamCache = TRUE;
                        g_Context->RamSlotOwner[ramSlot] = targetPageId;
                        if (oldOwner == (ULONG64)-1) g_Context->RamCachePages++;
                        WriteLog("[HyperRAM] Prefetch SUCCESS.\r\n");
                    }
                }
                KeReleaseSpinLock(&g_Context->Lock, oldIrql);
            } else {
                WriteLog("[HyperRAM] Prefetch failed to read from SSD pool file.\r\n");
            }
        }
    }
}

// --------------------------------------------------------------------------
// 5.  Read Dispatch  (cache hit / miss / prefetch trigger)
// --------------------------------------------------------------------------
NTSTATUS HyperRAM_Read(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_ PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);

    PIO_STACK_LOCATION stack  = IoGetCurrentIrpStackLocation(Irp);
    LONGLONG           offset = stack->Parameters.Read.ByteOffset.QuadPart;
    ULONG              length = stack->Parameters.Read.Length;
    ULONG64            pageId = (ULONG64)offset / PAGE_SIZE;

    NTSTATUS status = STATUS_SUCCESS;

    if (length == PAGE_SIZE && Irp->AssociatedIrp.SystemBuffer) {
        PUCHAR buf = (PUCHAR)Irp->AssociatedIrp.SystemBuffer;
        BOOLEAN hitRam = FALSE, hitSsd = FALSE;
        ULONG ssdOffset = 0;
        ULONG ramSlot = 0;

        KIRQL oldIrql;
        KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
        g_Context->TotalReads++;

        // BUG1 FIX: Linear probing — find the actual SSD slot for this pageId
        ULONG startSlot = (ULONG)(pageId % MAX_SSD_PAGES);
        ULONG slot = startSlot;
        BOOLEAN found = FALSE;
        for (ULONG i = 0; i < MAX_SSD_PAGES; i++) {
            if (g_Context->PageTable[slot].PageId == pageId) { found = TRUE; break; }
            if (g_Context->PageTable[slot].PageId == (ULONG64)-1) break;
            slot = (slot + 1) % MAX_SSD_PAGES;
        }

        if (found) {
            hitRam = g_Context->PageTable[slot].InRamCache;
            hitSsd = !hitRam && g_Context->PageTable[slot].InSsdPool;
            ssdOffset = g_Context->PageTable[slot].OffsetInSsd;
            // BUG1 FIX: RAM slot is derived from pageId, NOT the SSD slot
            ramSlot = (ULONG)(pageId % MAX_RAM_CACHE_PAGES);
        }

        if (hitRam || hitSsd) {
            if (hitRam) {
                g_Context->CacheHits++;
                // BUG1 FIX: read from the correct ram slot (pageId-based)
                PUCHAR src = (PUCHAR)g_Context->RamCacheBuffer + (ULONG64)ramSlot * PAGE_SIZE;
                RtlCopyMemory(buf, src, PAGE_SIZE);
                KeReleaseSpinLock(&g_Context->Lock, oldIrql);
                WriteLog("[HyperRAM] READ: RAM cache HIT.\r\n");
            }
            else {
                g_Context->CacheMisses++;
                g_Context->NvmeReads++;
                KeReleaseSpinLock(&g_Context->Lock, oldIrql);

                // Allocate buffer for compressed data
                PUCHAR compressed_buffer = (PUCHAR)ExAllocatePoolWithTag(NonPagedPoolNx, g_Context->PageTable[slot].DataLength, POOL_TAG);
                if (!compressed_buffer) {
                    status = STATUS_INSUFFICIENT_RESOURCES;
                    RtlZeroMemory(buf, PAGE_SIZE);
                    goto ReadEndDecompress;
                }

                // Read the compressed data from the pool file
                IO_STATUS_BLOCK ioStatus;
                LARGE_INTEGER byteOffset;
                byteOffset.QuadPart = (LONGLONG)ssdOffset;
                status = ZwReadFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                                    &ioStatus, compressed_buffer, g_Context->PageTable[slot].DataLength, &byteOffset, NULL);

                if (!NT_SUCCESS(status)) {
                    ExFreePoolWithTag(compressed_buffer, POOL_TAG);
                    WriteLog("[HyperRAM] Real-time READ from SSD pool file failed!\r\n");
                    RtlZeroMemory(buf, PAGE_SIZE);
                    goto ReadEndDecompress;
                }

                // Measure decompression time
                LARGE_INTEGER freqD;
                LARGE_INTEGER tDecompStart = KeQueryPerformanceCounter(&freqD);
                ULONG uncompressed_size = 0;
                NTSTATUS decomp_status = RtlDecompressBuffer(
                                            COMPRESSION_FORMAT_XPRESS,
                                            buf,   // output buffer
                                            PAGE_SIZE,   // output buffer size
                                            compressed_buffer,
                                            g_Context->PageTable[slot].DataLength,
                                            &uncompressed_size
                                        );
                LARGE_INTEGER tDecompEnd = KeQueryPerformanceCounter(NULL);
                ULONG64 decompUs = ((tDecompEnd.QuadPart - tDecompStart.QuadPart) * 1000000) / freqD.QuadPart;
                InterlockedAdd64((volatile LONG64*)&g_Context->TotalDecompressTimeUs, (LONG64)decompUs);

                ExFreePoolWithTag(compressed_buffer, POOL_TAG);

                if (!NT_SUCCESS(decomp_status) || uncompressed_size != PAGE_SIZE) {
                    WriteLog("[HyperRAM] Decompression failed!\r\n");
                    RtlZeroMemory(buf, PAGE_SIZE);
                    goto ReadEndDecompress;
                }

ReadEndDecompress:
                // Promote to RAM cache with correct slot index
                PUCHAR ramDst = (PUCHAR)g_Context->RamCacheBuffer + (ULONG64)ramSlot * PAGE_SIZE;
                RtlCopyMemory(ramDst, buf, PAGE_SIZE);

                KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
                // BUG1 ROOT-CAUSE FIX: use RamSlotOwner to evict prior occupant
                if (!g_Context->PageTable[slot].InRamCache) {
                    ULONG64 oldOwner = g_Context->RamSlotOwner[ramSlot];
                    if (oldOwner != (ULONG64)-1 && oldOwner != pageId) {
                        // Find old owner's SSD slot and clear its InRamCache
                        ULONG oldSsdBase = (ULONG)(oldOwner % MAX_SSD_PAGES);
                        for (ULONG oi = 0; oi < MAX_SSD_PAGES; oi++) {
                            ULONG chk = (oldSsdBase + oi) % MAX_SSD_PAGES;
                            if (g_Context->PageTable[chk].PageId == oldOwner) {
                                if (g_Context->PageTable[chk].InRamCache) {
                                    g_Context->PageTable[chk].InRamCache = FALSE;
                                    if (g_Context->RamCachePages > 0) g_Context->RamCachePages--;
                                }
                                break;
                            }
                            if (g_Context->PageTable[chk].PageId == (ULONG64)-1) break;
                        }
                    }
                    g_Context->PageTable[slot].InRamCache = TRUE;
                    g_Context->RamSlotOwner[ramSlot] = pageId;
                    if (oldOwner == (ULONG64)-1) g_Context->RamCachePages++;
                    // else: we replaced an old page — count stays the same
                }
                KeReleaseSpinLock(&g_Context->Lock, oldIrql);
                WriteLog("[HyperRAM] READ: SSD miss, promoted to RAM.\r\n");
            }

            // --- BUG3 FIX: TAU-BASED PREDICTIVE PREFETCHING (also in IRP path) ---
            LARGE_INTEGER freqP;
            LARGE_INTEGER now = KeQueryPerformanceCounter(&freqP);
            LONG depth = 0;
            BOOLEAN doPrefetch = FALSE;

            KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
            if (g_Context->HasLastAccess) {
                LONGLONG delta_us = ((now.QuadPart - g_Context->LastAccessTime.QuadPart) * 1000000) / freqP.QuadPart;
                if (delta_us > 0)
                    g_Context->InterArrivalTauUs = (85 * g_Context->InterArrivalTauUs + 15 * delta_us) / 100;
            }
            g_Context->HasLastAccess = TRUE;
            g_Context->LastAccessTime = now;

            LONG currentStride = (LONG)(pageId - g_Context->LastPageId);
            if (currentStride == g_Context->LastStride) {
                g_Context->StrideConfidence = g_Context->StrideConfidence < 8 ? g_Context->StrideConfidence + 1 : 8;
            } else {
                g_Context->StrideConfidence = g_Context->StrideConfidence > 2 ? g_Context->StrideConfidence - 2 : 0;
                g_Context->LastStride = currentStride;
            }
            g_Context->LastPageId = pageId;

            if (g_Context->StrideConfidence >= 3 && g_Context->LastStride != 0) {
                LONGLONG div = g_Context->InterArrivalTauUs + 1;
                depth = (LONG)(12000 / div);
                if (depth < 1) depth = 1;
                if (depth > 8) depth = 8;
                g_Context->PrefetchPageId = pageId;
                g_Context->PrefetchStride = g_Context->LastStride;
                g_Context->PrefetchDepth  = depth;
                if (!g_Context->PrefetchPending) {
                    g_Context->PrefetchPending = TRUE;
                    doPrefetch = TRUE;
                    g_Context->PrefetchesFired++;
                }
            }
            KeReleaseSpinLock(&g_Context->Lock, oldIrql);

            if (doPrefetch)
                IoQueueWorkItem(g_Context->PrefetchWorkItem, HyperRAM_PrefetchWorkItem, DelayedWorkQueue, NULL);

            Irp->IoStatus.Information = PAGE_SIZE;
            goto ReadDone;
        }

        g_Context->CacheMisses++;
        KeReleaseSpinLock(&g_Context->Lock, oldIrql);
        RtlZeroMemory(buf, PAGE_SIZE);
        WriteLog("[HyperRAM] READ: page not in cache, returning zeros.\r\n");
        Irp->IoStatus.Information = PAGE_SIZE;

ReadDone:
        ; // NOP target after read completion
    } else {
        status = STATUS_INVALID_PARAMETER;
        Irp->IoStatus.Information = 0;
    }

    Irp->IoStatus.Status = status;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return status;
}

// --------------------------------------------------------------------------
// 6.  Write Dispatch  (mock-compress and store in SSD pool)
// --------------------------------------------------------------------------
NTSTATUS HyperRAM_Write(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_ PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);

    PIO_STACK_LOCATION stack  = IoGetCurrentIrpStackLocation(Irp);
    LONGLONG           offset = stack->Parameters.Write.ByteOffset.QuadPart;
    ULONG              length = stack->Parameters.Write.Length;
    ULONG64            pageId = (ULONG64)offset / PAGE_SIZE;

    NTSTATUS status = STATUS_SUCCESS;

    if (length == PAGE_SIZE && Irp->AssociatedIrp.SystemBuffer) {
        PUCHAR  src    = (PUCHAR)Irp->AssociatedIrp.SystemBuffer;

        // BUG1 FIX: Linear probing for write slot in IRP_MJ_WRITE path
        KIRQL oldIrql;
        KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
        ULONG startSlot = (ULONG)(pageId % MAX_SSD_PAGES);
        ULONG slot = startSlot;
        BOOLEAN slotFound = FALSE;
        for (ULONG i = 0; i < MAX_SSD_PAGES; i++) {
            if (g_Context->PageTable[slot].PageId == pageId) { slotFound = TRUE; break; }
            if (g_Context->PageTable[slot].PageId == (ULONG64)-1) { slotFound = TRUE; break; }
            slot = (slot + 1) % MAX_SSD_PAGES;
        }
        if (!slotFound) slot = startSlot;
        g_Context->TotalWrites++;
        g_Context->NvmeWrites++;
        KeReleaseSpinLock(&g_Context->Lock, oldIrql);

        // Allocate buffer for compressed data (worst case: same size as uncompressed)
        PUCHAR compressed_buffer = (PUCHAR)ExAllocatePoolWithTag(NonPagedPoolNx, PAGE_SIZE, POOL_TAG);
        if (!compressed_buffer) {
            status = STATUS_INSUFFICIENT_RESOURCES;
            goto WriteCompletion;
        }

        // Compress the data
        ULONG compressed_size = 0;
        LARGE_INTEGER freqComp;
        LARGE_INTEGER tCompStart = KeQueryPerformanceCounter(&freqComp);
         NTSTATUS comp_status = RtlCompressBuffer(
                                 (USHORT)COMPRESSION_FORMAT_XPRESS,
                                 src,
                                 PAGE_SIZE,   // uncompressed size is PAGE_SIZE (4096)
                                 compressed_buffer,
                                 PAGE_SIZE,   // output buffer size
                                 PAGE_SIZE,   // UncompressedChunkSize
                                 &compressed_size,
                                 g_Context->Workspace
                             );
        LARGE_INTEGER tCompEnd = KeQueryPerformanceCounter(NULL);
        ULONG64 compUs = ((tCompEnd.QuadPart - tCompStart.QuadPart) * 1000000) / freqComp.QuadPart;
        InterlockedAdd64((volatile LONG64*)&g_Context->TotalCompressTimeUs, (LONG64)compUs);

        if (!NT_SUCCESS(comp_status)) {
            ExFreePoolWithTag(compressed_buffer, POOL_TAG);
            status = comp_status;
            goto WriteCompletion;
        }

        // Write the compressed data to the pool file
        IO_STATUS_BLOCK ioStatus;
        LARGE_INTEGER byteOffset;
        byteOffset.QuadPart = (LONGLONG)slot * SSD_PAGE_SIZE;
        status = ZwWriteFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                             &ioStatus, compressed_buffer, compressed_size, &byteOffset, NULL);

        ExFreePoolWithTag(compressed_buffer, POOL_TAG);

        if (!NT_SUCCESS(status)) {
            goto WriteCompletion;
        }

        // Update page table and stats
        KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
        // If this slot was displaced by a different page, clear its RAM state
        if (g_Context->PageTable[slot].PageId != pageId &&
            g_Context->PageTable[slot].PageId != (ULONG64)-1 &&
            g_Context->PageTable[slot].InRamCache) {
            g_Context->PageTable[slot].InRamCache = FALSE;
            if (g_Context->RamCachePages > 0) g_Context->RamCachePages--;
        }
        g_Context->PageTable[slot].PageId      = pageId;
        if (g_Context->PageTable[slot].InRamCache) {
            g_Context->PageTable[slot].InRamCache  = FALSE;
            if (g_Context->RamCachePages > 0) g_Context->RamCachePages--;
        }
        if (!g_Context->PageTable[slot].InSsdPool) {
            g_Context->PageTable[slot].InSsdPool   = TRUE;
            g_Context->PoolUsedBytes += compressed_size;
        }
        g_Context->PageTable[slot].OffsetInSsd = slot * SSD_PAGE_SIZE;
        g_Context->PageTable[slot].DataLength  = compressed_size;
        // BUG2 FIX: Track logical and physical bytes
        g_Context->TotalUncompressedBytes += PAGE_SIZE;
        g_Context->TotalCompressedBytes   += compressed_size;
        
        // PERSISTENT METADATA: Periodically save metadata (every 100 writes)
        if (g_Context->TotalWrites % 100 == 0) {
            KeReleaseSpinLock(&g_Context->Lock, oldIrql);
            SavePageTableMetadata();
            KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
        }
        
        KeReleaseSpinLock(&g_Context->Lock, oldIrql);

        WriteLog("[HyperRAM] WRITE: page stored in real SSD pool file (compressed).\r\n");
    } else {
        status = STATUS_INVALID_PARAMETER;
    }

WriteCompletion:
    if (NT_SUCCESS(status)) {
        Irp->IoStatus.Information = length;
    } else {
        Irp->IoStatus.Information = 0;
    }
    Irp->IoStatus.Status = status;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return status;
}

// --------------------------------------------------------------------------
// 7.  DeviceControl  (IOCTL handler — extensible)
// --------------------------------------------------------------------------
NTSTATUS HyperRAM_DeviceControl(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_ PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    ULONG code = stack->Parameters.DeviceIoControl.IoControlCode;
    ULONG inLen = stack->Parameters.DeviceIoControl.InputBufferLength;
    ULONG outLen = stack->Parameters.DeviceIoControl.OutputBufferLength;
    PVOID buf = Irp->AssociatedIrp.SystemBuffer;
    NTSTATUS status = STATUS_SUCCESS;
    ULONG_PTR info = 0;

    WriteLog("[HyperRAM] DeviceControl IOCTL received.\r\n");

    // SECURITY: Validate driver context
    if (!g_Context) {
        Irp->IoStatus.Status = STATUS_DEVICE_NOT_READY;
        Irp->IoStatus.Information = 0;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return STATUS_DEVICE_NOT_READY;
    }

    // SECURITY: Validate buffer pointer
    if (code != IOCTL_HYPERRAM_FLUSH && code != IOCTL_HYPERRAM_GET_STATS) {
        if (!buf) {
            Irp->IoStatus.Status = STATUS_INVALID_PARAMETER;
            Irp->IoStatus.Information = 0;
            IoCompleteRequest(Irp, IO_NO_INCREMENT);
            return STATUS_INVALID_PARAMETER;
        }
    }

    // SECURITY: Validate buffer lengths for each IOCTL
    switch (code) {
    case IOCTL_HYPERRAM_GET_STATS:
        // SECURITY: Validate output buffer size
        if (outLen < sizeof(HYPERRAM_STATS)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }
        if (buf) {
            PHYPERRAM_STATS stats = (PHYPERRAM_STATS)buf;
            RtlZeroMemory(stats, sizeof(HYPERRAM_STATS));

            KIRQL oldIrql;
            KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
            stats->TotalReads       = g_Context->TotalReads;
            stats->TotalWrites      = g_Context->TotalWrites;
            stats->CacheHits        = g_Context->CacheHits;
            stats->CacheMisses      = g_Context->CacheMisses;
            stats->NvmeReads        = g_Context->NvmeReads;
            stats->NvmeWrites       = g_Context->NvmeWrites;
            stats->TauUs            = (ULONG64)g_Context->InterArrivalTauUs;
            stats->PoolSizeBytes    = SSD_POOL_SIZE;
            stats->PoolUsedBytes    = g_Context->PoolUsedBytes;
            stats->PrefetchesFired  = g_Context->PrefetchesFired;
            stats->StrideConfidence = (ULONG)g_Context->StrideConfidence;
            stats->LastStride       = g_Context->LastStride;
            // BUG1 FIX: hard clamp before export — counter can never exceed capacity
            stats->RamCachePages    = g_Context->RamCachePages <= MAX_RAM_CACHE_PAGES
                                        ? g_Context->RamCachePages : MAX_RAM_CACHE_PAGES;
            stats->MaxRamCachePages = MAX_RAM_CACHE_PAGES;
            stats->PageSize         = PAGE_SIZE;
            // BUG2 FIX: export I/O timing counters
            stats->TotalCompressTimeUs   = g_Context->TotalCompressTimeUs;
            stats->TotalDecompressTimeUs = g_Context->TotalDecompressTimeUs;
            stats->TotalCompressedBytes  = g_Context->TotalCompressedBytes;
            stats->TotalUncompressedBytes= g_Context->TotalUncompressedBytes;
            KeReleaseSpinLock(&g_Context->Lock, oldIrql);

            info = sizeof(HYPERRAM_STATS);
            status = STATUS_SUCCESS;
        } else {
            status = STATUS_INVALID_PARAMETER;
        }
        break;

    case IOCTL_HYPERRAM_FLUSH:
        WriteLog("[HyperRAM] Flush IOCTL received (mock NOP).\r\n");
        status = STATUS_SUCCESS;
        break;

    case IOCTL_HYPERRAM_SAVE_METADATA:
        // SECURITY: No input/output required
        WriteLog("[HyperRAM] Save Metadata IOCTL received.\r\n");
        SavePageTableMetadata();
        status = STATUS_SUCCESS;
        break;

    case IOCTL_HYPERRAM_RESIZE_POOL:
        // SECURITY: Strict input buffer validation
        if (inLen < sizeof(HYPERRAM_RESIZE_REQUEST) ||
            inLen > sizeof(HYPERRAM_RESIZE_REQUEST)) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        // SECURITY: Validate pool size bounds (prevent DoS)
        PHYPERRAM_RESIZE_REQUEST resizeReq = (PHYPERRAM_RESIZE_REQUEST)buf;
        if (resizeReq->NewPoolSizeGB == 0 || resizeReq->NewPoolSizeGB > 1024) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        WriteLog("[HyperRAM] Resize Pool IOCTL received (mock success, managed by userspace).\r\n");
        status = STATUS_SUCCESS;
        break;

    case IOCTL_HYPERRAM_READ_PAGE:
        // SECURITY: Strict input/output buffer validation
        if (inLen < sizeof(HYPERRAM_PAGE_REQUEST) ||
            inLen > sizeof(HYPERRAM_PAGE_REQUEST)) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        if (outLen < PAGE_SIZE) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }
        // SECURITY: Validate page request structure
        PHYPERRAM_PAGE_REQUEST req = (PHYPERRAM_PAGE_REQUEST)buf;
        if (req->DataLengthBytes != PAGE_SIZE) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        // SECURITY: Validate QoS tag bounds
        if (req->QoSTag > 5) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        if (buf) {
            // Capture PageId BEFORE any output write overwrites the shared buffer.
            ULONG64 pageId = req->PageId;
            PUCHAR outBuf = (PUCHAR)buf; // METHOD_BUFFERED: in/out share same buffer

            BOOLEAN hitRam = FALSE, hitSsd = FALSE;
            ULONG ssdOffset = 0;

            KIRQL oldIrql;
            KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
            g_Context->TotalReads++;

            // FIX C1: Linear probing — find the slot that actually holds pageId
            ULONG startSlot = (ULONG)(pageId % MAX_SSD_PAGES);
            ULONG slot = startSlot;
            BOOLEAN found = FALSE;

            for (ULONG i = 0; i < MAX_SSD_PAGES; i++) {
                if (g_Context->PageTable[slot].PageId == pageId) {
                    found = TRUE;
                    break;
                }
                if (g_Context->PageTable[slot].PageId == (ULONG64)-1) {
                    break; // Stop searching at first empty slot
                }
                slot = (slot + 1) % MAX_SSD_PAGES;
            }

            if (found) {
                hitRam = g_Context->PageTable[slot].InRamCache;
                hitSsd = !hitRam && g_Context->PageTable[slot].InSsdPool;
                ssdOffset = g_Context->PageTable[slot].OffsetInSsd;
            }

            // BUG1 FIX: RAM slot derived from pageId, not SSD slot
            ULONG ramSlot = (ULONG)(pageId % MAX_RAM_CACHE_PAGES);

            if (hitRam || hitSsd) {
                if (hitRam) {
                    g_Context->CacheHits++;
                    // BUG1 FIX: read from pageId-based RAM slot
                    PUCHAR src = (PUCHAR)g_Context->RamCacheBuffer + (ULONG64)ramSlot * PAGE_SIZE;
                    RtlCopyMemory(outBuf, src, PAGE_SIZE);
                    KeReleaseSpinLock(&g_Context->Lock, oldIrql);
                    WriteLog("[HyperRAM] IOCTL READ: RAM cache HIT.\r\n");
                }
                else {
                    g_Context->CacheMisses++;
                    g_Context->NvmeReads++;
                    KeReleaseSpinLock(&g_Context->Lock, oldIrql);

                // Allocate buffer for compressed data
                PUCHAR compressed_buffer = (PUCHAR)ExAllocatePoolWithTag(NonPagedPoolNx, g_Context->PageTable[slot].DataLength, POOL_TAG);
                if (!compressed_buffer) {
                    status = STATUS_INSUFFICIENT_RESOURCES;
                    RtlZeroMemory(outBuf, PAGE_SIZE);
                    goto ReadEndDecompress_IOCTL;
                }

                // Read the compressed data from the pool file
                IO_STATUS_BLOCK ioStatus;
                LARGE_INTEGER byteOffset;
                byteOffset.QuadPart = (LONGLONG)ssdOffset;
                status = ZwReadFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                                    &ioStatus, compressed_buffer, g_Context->PageTable[slot].DataLength, &byteOffset, NULL);

                if (!NT_SUCCESS(status)) {
                    ExFreePoolWithTag(compressed_buffer, POOL_TAG);
                    WriteLog("[HyperRAM] IOCTL READ from SSD pool file failed!\r\n");
                    RtlZeroMemory(outBuf, PAGE_SIZE);
                    goto ReadEndDecompress_IOCTL;
                }

                // Measure decompression time
                LARGE_INTEGER freqD;
                LARGE_INTEGER tDecompStart = KeQueryPerformanceCounter(&freqD);
                ULONG uncompressed_size = 0;
                NTSTATUS decomp_status = RtlDecompressBuffer(
                                            COMPRESSION_FORMAT_XPRESS,
                                            outBuf,   // output buffer
                                            PAGE_SIZE,   // output buffer size
                                            compressed_buffer,
                                            g_Context->PageTable[slot].DataLength,
                                            &uncompressed_size
                                        );
                LARGE_INTEGER tDecompEnd = KeQueryPerformanceCounter(NULL);
                ULONG64 decompUs = ((tDecompEnd.QuadPart - tDecompStart.QuadPart) * 1000000) / freqD.QuadPart;
                InterlockedAdd64((volatile LONG64*)&g_Context->TotalDecompressTimeUs, (LONG64)decompUs);

                ExFreePoolWithTag(compressed_buffer, POOL_TAG);

                if (!NT_SUCCESS(decomp_status) || uncompressed_size != PAGE_SIZE) {
                    WriteLog("[HyperRAM] Decompression failed!\r\n");
                    RtlZeroMemory(outBuf, PAGE_SIZE);
                    goto ReadEndDecompress_IOCTL;
                }

ReadEndDecompress_IOCTL:
                    // BUG1 FIX: promote to pageId-based RAM slot
                    PUCHAR ramDst = (PUCHAR)g_Context->RamCacheBuffer + (ULONG64)ramSlot * PAGE_SIZE;
                    RtlCopyMemory(ramDst, outBuf, PAGE_SIZE);

                KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
                // BUG1 ROOT-CAUSE FIX: use RamSlotOwner for IOCTL read path
                if (!g_Context->PageTable[slot].InRamCache) {
                    ULONG64 oldOwner = g_Context->RamSlotOwner[ramSlot];
                    if (oldOwner != (ULONG64)-1 && oldOwner != pageId) {
                        ULONG oldSsdBase = (ULONG)(oldOwner % MAX_SSD_PAGES);
                        for (ULONG oi = 0; oi < MAX_SSD_PAGES; oi++) {
                            ULONG chk = (oldSsdBase + oi) % MAX_SSD_PAGES;
                            if (g_Context->PageTable[chk].PageId == oldOwner) {
                                if (g_Context->PageTable[chk].InRamCache) {
                                    g_Context->PageTable[chk].InRamCache = FALSE;
                                    if (g_Context->RamCachePages > 0) g_Context->RamCachePages--;
                                }
                                break;
                            }
                            if (g_Context->PageTable[chk].PageId == (ULONG64)-1) break;
                        }
                    }
                    g_Context->PageTable[slot].InRamCache = TRUE;
                    g_Context->RamSlotOwner[ramSlot] = pageId;
                    if (oldOwner == (ULONG64)-1) g_Context->RamCachePages++;
                }
                KeReleaseSpinLock(&g_Context->Lock, oldIrql);
                WriteLog("[HyperRAM] IOCTL READ: SSD miss, promoted to RAM.\r\n");
                }

                // BUG3 FIX: Tau predictor also in IOCTL path
                LARGE_INTEGER freqP;
                LARGE_INTEGER now = KeQueryPerformanceCounter(&freqP);
                LONG depth = 0;
                BOOLEAN doPrefetch = FALSE;

                KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
                if (g_Context->HasLastAccess) {
                    LONGLONG delta_us = ((now.QuadPart - g_Context->LastAccessTime.QuadPart) * 1000000) / freqP.QuadPart;
                    if (delta_us > 0)
                        g_Context->InterArrivalTauUs = (85 * g_Context->InterArrivalTauUs + 15 * delta_us) / 100;
                }
                g_Context->HasLastAccess = TRUE;
                g_Context->LastAccessTime = now;

                LONG currentStride = (LONG)(pageId - g_Context->LastPageId);
                if (currentStride == g_Context->LastStride) {
                    g_Context->StrideConfidence = g_Context->StrideConfidence < 8 ? g_Context->StrideConfidence + 1 : 8;
                } else {
                    g_Context->StrideConfidence = g_Context->StrideConfidence > 2 ? g_Context->StrideConfidence - 2 : 0;
                    g_Context->LastStride = currentStride;
                }
                g_Context->LastPageId = pageId;

                if (g_Context->StrideConfidence >= 3 && g_Context->LastStride != 0) {
                    LONGLONG div = g_Context->InterArrivalTauUs + 1;
                    depth = (LONG)(12000 / div);
                    if (depth < 1) depth = 1;
                    if (depth > 8) depth = 8;
                    g_Context->PrefetchPageId = pageId;
                    g_Context->PrefetchStride = g_Context->LastStride;
                    g_Context->PrefetchDepth  = depth;
                    if (!g_Context->PrefetchPending) {
                        g_Context->PrefetchPending = TRUE;
                        doPrefetch = TRUE;
                        g_Context->PrefetchesFired++;
                    }
                }
                KeReleaseSpinLock(&g_Context->Lock, oldIrql);

                if (doPrefetch)
                    IoQueueWorkItem(g_Context->PrefetchWorkItem, HyperRAM_PrefetchWorkItem, DelayedWorkQueue, NULL);

            } else {
                g_Context->CacheMisses++;
                KeReleaseSpinLock(&g_Context->Lock, oldIrql);
                RtlZeroMemory(outBuf, PAGE_SIZE);
                WriteLog("[HyperRAM] IOCTL READ: page not cached, zeros returned.\r\n");
            }
            info = PAGE_SIZE;
            status = STATUS_SUCCESS;
        } else {
            status = STATUS_INVALID_PARAMETER;
        }
        break;

    case IOCTL_HYPERRAM_WRITE_PAGE:
        // SECURITY: Strict input buffer validation - exact size check
        if (inLen != sizeof(HYPERRAM_PAGE_REQUEST) + PAGE_SIZE) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        // SECURITY: Validate output buffer (should be 0 for write)
        if (outLen != 0) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        // SECURITY: Validate page request structure
        PHYPERRAM_PAGE_REQUEST req = (PHYPERRAM_PAGE_REQUEST)buf;
        if (req->DataLengthBytes != PAGE_SIZE) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        // SECURITY: Validate QoS tag bounds
        if (req->QoSTag > 5) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        if (buf) {
            ULONG64 pageId = req->PageId;
            PUCHAR srcData = (PUCHAR)buf + sizeof(HYPERRAM_PAGE_REQUEST);

            KIRQL oldIrql;
            KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
            
            // FIX C1: Linear probing for write
            ULONG startSlot = (ULONG)(pageId % MAX_SSD_PAGES);
            ULONG slot = startSlot;
            BOOLEAN found = FALSE;

            for (ULONG i = 0; i < MAX_SSD_PAGES; i++) {
                if (g_Context->PageTable[slot].PageId == pageId) {
                    found = TRUE;
                    break; // Found existing entry to update
                }
                if (g_Context->PageTable[slot].PageId == (ULONG64)-1) {
                    found = TRUE;
                    break; // Found empty slot
                }
                slot = (slot + 1) % MAX_SSD_PAGES;
            }

            if (!found) {
                slot = startSlot; // Fallback if pool is full
            }

            g_Context->TotalWrites++;
            g_Context->NvmeWrites++;
            KeReleaseSpinLock(&g_Context->Lock, oldIrql);

             // Allocate buffer for compressed data (worst case: same size as uncompressed)
        PUCHAR compressed_buffer = (PUCHAR)ExAllocatePoolWithTag(NonPagedPoolNx, PAGE_SIZE, POOL_TAG);
             if (!compressed_buffer) {
                 status = STATUS_INSUFFICIENT_RESOURCES;
                 goto WriteEnd_IOCTL;
             }

             // Compress the data
             ULONG compressed_size = 0;
             LARGE_INTEGER freqComp;
             LARGE_INTEGER tCompStart = KeQueryPerformanceCounter(&freqComp);
             NTSTATUS comp_status = RtlCompressBuffer(
                                     COMPRESSION_FORMAT_XPRESS,
                                     srcData,
                                     PAGE_SIZE,   // uncompressed size is PAGE_SIZE (4096)
                                     compressed_buffer,
                                     PAGE_SIZE,   // output buffer size
                                     PAGE_SIZE,   // UncompressedChunkSize
                                     &compressed_size,
                                     g_Context->Workspace
                                 );
             LARGE_INTEGER tCompEnd = KeQueryPerformanceCounter(NULL);
             ULONG64 compUs = ((tCompEnd.QuadPart - tCompStart.QuadPart) * 1000000) / freqComp.QuadPart;
             InterlockedAdd64((volatile LONG64*)&g_Context->TotalCompressTimeUs, (LONG64)compUs);

             if (!NT_SUCCESS(comp_status)) {
                 ExFreePoolWithTag(compressed_buffer, POOL_TAG);
                 status = comp_status;
                 goto WriteEnd_IOCTL;
             }

             // Write the compressed data to the pool file
             IO_STATUS_BLOCK ioStatus;
             LARGE_INTEGER byteOffset;
             byteOffset.QuadPart = (LONGLONG)slot * SSD_PAGE_SIZE;
             status = ZwWriteFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                                  &ioStatus, compressed_buffer, compressed_size, &byteOffset, NULL);

             ExFreePoolWithTag(compressed_buffer, POOL_TAG);

if (!NT_SUCCESS(status)) {
                  goto WriteEnd_IOCTL;
              }

              // Update page table and stats
              KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
              // If this slot was displaced by a different page, clear its RAM state
              if (g_Context->PageTable[slot].PageId != pageId &&
                  g_Context->PageTable[slot].PageId != (ULONG64)-1 &&
                  g_Context->PageTable[slot].InRamCache) {
                  g_Context->PageTable[slot].InRamCache = FALSE;
                  if (g_Context->RamCachePages > 0) g_Context->RamCachePages--;
              }
              g_Context->PageTable[slot].PageId      = pageId;
              if (g_Context->PageTable[slot].InRamCache) {
                  g_Context->PageTable[slot].InRamCache  = FALSE;
                  if (g_Context->RamCachePages > 0) g_Context->RamCachePages--;
              }
              if (!g_Context->PageTable[slot].InSsdPool) {
                  g_Context->PageTable[slot].InSsdPool   = TRUE;
                  g_Context->PoolUsedBytes += compressed_size;
              }
              g_Context->PageTable[slot].OffsetInSsd = slot * SSD_PAGE_SIZE;
              g_Context->PageTable[slot].DataLength  = compressed_size;
              // BUG2 FIX: Track logical and physical bytes
              g_Context->TotalUncompressedBytes += PAGE_SIZE;
              g_Context->TotalCompressedBytes   += compressed_size;
              
// PERSISTENT METADATA: Periodically save (every 100 writes)
              if (g_Context->TotalWrites % 100 == 0) {
                  KeReleaseSpinLock(&g_Context->Lock, oldIrql);
                  SavePageTableMetadata();
                  KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
              }
              
              KeReleaseSpinLock(&g_Context->Lock, oldIrql);

              WriteLog("[HyperRAM] IOCTL WRITE: page stored in real SSD pool file (compressed).\r\n");
              status = STATUS_SUCCESS;
         } else {
             status = STATUS_INVALID_PARAMETER;
         }
WriteEnd_IOCTL:
    break;

    default:
        WriteLog("[HyperRAM] Unknown IOCTL code.\r\n");
        status = STATUS_INVALID_DEVICE_REQUEST;
        break;
    }

    Irp->IoStatus.Status = status;
    Irp->IoStatus.Information = info;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return status;
}
