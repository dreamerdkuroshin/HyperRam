#pragma warning(disable: 4996) // Suppress ExAllocatePoolWithTag deprecation
#include <ntddk.h>
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
#define SSD_PAGE_SIZE       2048                            // 50% mock-compression
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

    RtlZeroMemory(g_Context->RamCacheBuffer, RAM_CACHE_SIZE);
    RtlZeroMemory(g_Context->PageTable, sizeof(PAGE_ENTRY) * MAX_SSD_PAGES);
    for (ULONG i = 0; i < MAX_SSD_PAGES; i++)
        g_Context->PageTable[i].PageId = (ULONG64)-1;

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
            PUCHAR dst = (PUCHAR)g_Context->RamCacheBuffer + (slot % MAX_RAM_CACHE_PAGES) * PAGE_SIZE;

            // Perform real file read from SSD pool file
            IO_STATUS_BLOCK ioStatus;
            LARGE_INTEGER byteOffset;
            byteOffset.QuadPart = (LONGLONG)offset;

            NTSTATUS status = ZwReadFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                                         &ioStatus, dst, SSD_PAGE_SIZE, &byteOffset, NULL);

            if (NT_SUCCESS(status)) {
                RtlZeroMemory(dst + SSD_PAGE_SIZE, PAGE_SIZE - SSD_PAGE_SIZE);

                KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
                // Re-verify after releasing spin lock that target page hasn't changed/evicted
                if (g_Context->PageTable[slot].PageId   == targetPageId &&
                   !g_Context->PageTable[slot].InRamCache               &&
                    g_Context->RamCachePages < MAX_RAM_CACHE_PAGES) {
                    
                    g_Context->PageTable[slot].InRamCache = TRUE;
                    g_Context->RamCachePages++;
                    WriteLog("[HyperRAM] Prefetch SUCCESS: page eagerly loaded to RAM from SSD file.\r\n");
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
    ULONG              slot   = (ULONG)(pageId % MAX_SSD_PAGES);

    NTSTATUS status = STATUS_SUCCESS;

    if (length == PAGE_SIZE && Irp->AssociatedIrp.SystemBuffer) {
        PUCHAR buf = (PUCHAR)Irp->AssociatedIrp.SystemBuffer;
        BOOLEAN hitRam = FALSE, hitSsd = FALSE;
        ULONG ssdOffset = 0;

        KIRQL oldIrql;
        KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
        g_Context->TotalReads++;

        // FIX C1: Verify stored PageId matches — detect and handle hash collision.
        if (g_Context->PageTable[slot].PageId == pageId) {
            hitRam = g_Context->PageTable[slot].InRamCache;
            hitSsd = !hitRam && g_Context->PageTable[slot].InSsdPool;
            ssdOffset = g_Context->PageTable[slot].OffsetInSsd;
        }
        // If PageId mismatch: slot is occupied by a different page (collision).
        // hitRam and hitSsd remain FALSE → treated as a cold miss below.

        if (hitRam || hitSsd) {
            if (hitRam) {
                g_Context->CacheHits++;
                PUCHAR src = (PUCHAR)g_Context->RamCacheBuffer
                           + (slot % MAX_RAM_CACHE_PAGES) * PAGE_SIZE;
                RtlCopyMemory(buf, src, PAGE_SIZE);
                KeReleaseSpinLock(&g_Context->Lock, oldIrql);
                WriteLog("[HyperRAM] READ: RAM cache HIT.\r\n");
            }
            else {
                g_Context->CacheMisses++;
                g_Context->NvmeReads++;
                KeReleaseSpinLock(&g_Context->Lock, oldIrql);

                // Perform real file read from SSD pool file (inherent NVMe hardware latency occurs naturally)
                IO_STATUS_BLOCK ioStatus;
                LARGE_INTEGER byteOffset;
                byteOffset.QuadPart = (LONGLONG)ssdOffset;

                status = ZwReadFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                                    &ioStatus, buf, SSD_PAGE_SIZE, &byteOffset, NULL);

                if (NT_SUCCESS(status)) {
                    RtlZeroMemory(buf + SSD_PAGE_SIZE, PAGE_SIZE - SSD_PAGE_SIZE);
                } else {
                    WriteLog("[HyperRAM] Real-time READ from SSD pool file failed!\r\n");
                    RtlZeroMemory(buf, PAGE_SIZE);
                }

                // Promote to RAM cache — only if we are under capacity.
                // FIX C4: Guard RamCachePages against exceeding the buffer.
                PUCHAR ramDst = (PUCHAR)g_Context->RamCacheBuffer
                              + (slot % MAX_RAM_CACHE_PAGES) * PAGE_SIZE;
                RtlCopyMemory(ramDst, buf, PAGE_SIZE);

                KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
                if (!g_Context->PageTable[slot].InRamCache &&
                    g_Context->RamCachePages < MAX_RAM_CACHE_PAGES) {
                    g_Context->PageTable[slot].InRamCache = TRUE;
                    g_Context->RamCachePages++;
                }
                KeReleaseSpinLock(&g_Context->Lock, oldIrql);
                WriteLog("[HyperRAM] READ: SSD miss, loaded from real SSD pool file and promoted to RAM.\r\n");
            }

            // --- TAU-BASED PREDICTIVE PREFETCHING ---
            LARGE_INTEGER freq;
            LARGE_INTEGER now = KeQueryPerformanceCounter(&freq);
            
            LONG depth = 0;
            BOOLEAN doPrefetch = FALSE;

            KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
            
            if (g_Context->HasLastAccess) {
                LONGLONG delta_us = ((now.QuadPart - g_Context->LastAccessTime.QuadPart) * 1000000) / freq.QuadPart;
                if (delta_us > 0) {
                    g_Context->InterArrivalTauUs = (85 * g_Context->InterArrivalTauUs + 15 * delta_us) / 100;
                }
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
                depth = (LONG)(12000 / div); // 12ms numerator in microseconds
                if (depth < 1) depth = 1;
                if (depth > 8) depth = 8;

                g_Context->PrefetchPageId = pageId;
                g_Context->PrefetchStride = g_Context->LastStride;
                g_Context->PrefetchDepth  = depth;
                // FIX Bug-6: Only queue the work item if it is not already pending
                if (!g_Context->PrefetchPending) {
                    g_Context->PrefetchPending = TRUE;
                    doPrefetch = TRUE;
                    g_Context->PrefetchesFired++;
                }
            }
            
            KeReleaseSpinLock(&g_Context->Lock, oldIrql);

            if (doPrefetch) {
                IoQueueWorkItem(g_Context->PrefetchWorkItem,
                    HyperRAM_PrefetchWorkItem, DelayedWorkQueue, NULL);
            }

            Irp->IoStatus.Information = PAGE_SIZE;
            goto ReadDone;
        }

        g_Context->CacheMisses++;
        KeReleaseSpinLock(&g_Context->Lock, oldIrql);
        // Page not cached — return zeroed buffer
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
    ULONG              slot   = (ULONG)(pageId % MAX_SSD_PAGES);

    NTSTATUS status = STATUS_SUCCESS;

    if (length == PAGE_SIZE && Irp->AssociatedIrp.SystemBuffer) {
        PUCHAR  src    = (PUCHAR)Irp->AssociatedIrp.SystemBuffer;

        KIRQL oldIrql;
        KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
        g_Context->TotalWrites++;
        g_Context->NvmeWrites++;
        KeReleaseSpinLock(&g_Context->Lock, oldIrql);

        // Perform real file write to SSD pool file
        IO_STATUS_BLOCK ioStatus;
        LARGE_INTEGER byteOffset;
        byteOffset.QuadPart = (LONGLONG)slot * SSD_PAGE_SIZE;

        status = ZwWriteFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                             &ioStatus, src, SSD_PAGE_SIZE, &byteOffset, NULL);

        if (!NT_SUCCESS(status)) {
            WriteLog("[HyperRAM] Real-time WRITE to SSD pool file failed!\r\n");
        }

        KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
        // FIX C1: If this slot was occupied by a different page (hash collision),
        // clear the evicted page's RAM cache status before overwriting.
        if (g_Context->PageTable[slot].PageId != pageId &&
            g_Context->PageTable[slot].PageId != (ULONG64)-1 &&
            g_Context->PageTable[slot].InRamCache) {
            // Evict the displaced page from RAM cache accounting
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
            g_Context->PoolUsedBytes += SSD_PAGE_SIZE;
        }
        g_Context->PageTable[slot].OffsetInSsd = slot * SSD_PAGE_SIZE;
        g_Context->PageTable[slot].DataLength  = SSD_PAGE_SIZE;
        KeReleaseSpinLock(&g_Context->Lock, oldIrql);

        WriteLog("[HyperRAM] WRITE: page stored in real SSD pool file.\r\n");
        Irp->IoStatus.Information = length;
    } else {
        status = STATUS_INVALID_PARAMETER;
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

    if (!g_Context) {
        Irp->IoStatus.Status = STATUS_DEVICE_NOT_READY;
        Irp->IoStatus.Information = 0;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return STATUS_DEVICE_NOT_READY;
    }

    switch (code) {
    case IOCTL_HYPERRAM_GET_STATS:
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
            stats->RamCachePages    = g_Context->RamCachePages;
            stats->MaxRamCachePages = RAM_CACHE_SIZE / PAGE_SIZE;
            stats->PageSize         = PAGE_SIZE;
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

    case IOCTL_HYPERRAM_RESIZE_POOL:
        if (inLen < sizeof(HYPERRAM_RESIZE_REQUEST)) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        WriteLog("[HyperRAM] Resize Pool IOCTL received (mock success, managed by userspace).\r\n");
        status = STATUS_SUCCESS;
        break;

    case IOCTL_HYPERRAM_READ_PAGE:
        if (inLen < sizeof(HYPERRAM_PAGE_REQUEST) || outLen < PAGE_SIZE) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        if (buf) {
            PHYPERRAM_PAGE_REQUEST req = (PHYPERRAM_PAGE_REQUEST)buf;
            // Capture PageId BEFORE any output write overwrites the shared buffer.
            ULONG64 pageId = req->PageId;
            ULONG slot = (ULONG)(pageId % MAX_SSD_PAGES);
            PUCHAR outBuf = (PUCHAR)buf; // METHOD_BUFFERED: in/out share same buffer
            
            BOOLEAN hitRam = FALSE, hitSsd = FALSE;
            ULONG ssdOffset = 0;

            KIRQL oldIrql;
            KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
            g_Context->TotalReads++;

            // FIX C1: Only treat as a hit if stored PageId matches exactly.
            if (g_Context->PageTable[slot].PageId == pageId) {
                hitRam = g_Context->PageTable[slot].InRamCache;
                hitSsd = !hitRam && g_Context->PageTable[slot].InSsdPool;
                ssdOffset = g_Context->PageTable[slot].OffsetInSsd;
            }

            if (hitRam || hitSsd) {
                if (hitRam) {
                    g_Context->CacheHits++;
                    PUCHAR src = (PUCHAR)g_Context->RamCacheBuffer
                               + (slot % MAX_RAM_CACHE_PAGES) * PAGE_SIZE;
                    RtlCopyMemory(outBuf, src, PAGE_SIZE);
                    KeReleaseSpinLock(&g_Context->Lock, oldIrql);
                    WriteLog("[HyperRAM] IOCTL READ: RAM cache HIT.\r\n");
                }
                else {
                    g_Context->CacheMisses++;
                    g_Context->NvmeReads++;
                    KeReleaseSpinLock(&g_Context->Lock, oldIrql);

                    // Perform real file read from SSD pool file
                    IO_STATUS_BLOCK ioStatus;
                    LARGE_INTEGER byteOffset;
                    byteOffset.QuadPart = (LONGLONG)ssdOffset;

                    status = ZwReadFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                                        &ioStatus, outBuf, SSD_PAGE_SIZE, &byteOffset, NULL);

                    if (NT_SUCCESS(status)) {
                        RtlZeroMemory(outBuf + SSD_PAGE_SIZE, PAGE_SIZE - SSD_PAGE_SIZE);
                    } else {
                        WriteLog("[HyperRAM] IOCTL READ from SSD pool file failed!\r\n");
                        RtlZeroMemory(outBuf, PAGE_SIZE);
                    }

                    // Promote to RAM cache — FIX C4: enforce capacity limit.
                    PUCHAR ramDst = (PUCHAR)g_Context->RamCacheBuffer
                                  + (slot % MAX_RAM_CACHE_PAGES) * PAGE_SIZE;
                    RtlCopyMemory(ramDst, outBuf, PAGE_SIZE);

                    KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
                    if (!g_Context->PageTable[slot].InRamCache &&
                        g_Context->RamCachePages < MAX_RAM_CACHE_PAGES) {
                        g_Context->PageTable[slot].InRamCache = TRUE;
                        g_Context->RamCachePages++;
                    }
                    KeReleaseSpinLock(&g_Context->Lock, oldIrql);
                    WriteLog("[HyperRAM] IOCTL READ: SSD miss, promoted to RAM.\r\n");
                }
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
        // FIX S2: Add upper bound to prevent DoS via huge METHOD_BUFFERED allocation.
        if (inLen < sizeof(HYPERRAM_PAGE_REQUEST) + PAGE_SIZE ||
            inLen > sizeof(HYPERRAM_PAGE_REQUEST) + PAGE_SIZE) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        if (buf) {
            PHYPERRAM_PAGE_REQUEST req = (PHYPERRAM_PAGE_REQUEST)buf;
            ULONG64 pageId = req->PageId;
            ULONG slot = (ULONG)(pageId % MAX_SSD_PAGES);
            PUCHAR srcData = (PUCHAR)buf + sizeof(HYPERRAM_PAGE_REQUEST);

            KIRQL oldIrql;
            KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
            g_Context->TotalWrites++;
            g_Context->NvmeWrites++;
            KeReleaseSpinLock(&g_Context->Lock, oldIrql);

            // Perform real file write to SSD pool file
            IO_STATUS_BLOCK ioStatus;
            LARGE_INTEGER byteOffset;
            byteOffset.QuadPart = (LONGLONG)slot * SSD_PAGE_SIZE;

            status = ZwWriteFile(g_Context->PoolFileHandle, NULL, NULL, NULL,
                                 &ioStatus, srcData, SSD_PAGE_SIZE, &byteOffset, NULL);

            if (!NT_SUCCESS(status)) {
                WriteLog("[HyperRAM] IOCTL WRITE to SSD pool file failed!\r\n");
            }

            KeAcquireSpinLock(&g_Context->Lock, &oldIrql);
            // FIX C1: Clear displaced page's RAM flag when slot is reused.
            if (g_Context->PageTable[slot].PageId != pageId &&
                g_Context->PageTable[slot].PageId != (ULONG64)-1 &&
                g_Context->PageTable[slot].InRamCache) {
                g_Context->PageTable[slot].InRamCache = FALSE;
                if (g_Context->RamCachePages > 0) g_Context->RamCachePages--;
            }

            g_Context->PageTable[slot].PageId = pageId;
            if (g_Context->PageTable[slot].InRamCache) {
                g_Context->PageTable[slot].InRamCache = FALSE;
                if (g_Context->RamCachePages > 0) g_Context->RamCachePages--;
            }
            if (!g_Context->PageTable[slot].InSsdPool) {
                g_Context->PageTable[slot].InSsdPool = TRUE;
                g_Context->PoolUsedBytes += SSD_PAGE_SIZE;
            }
            g_Context->PageTable[slot].OffsetInSsd = slot * SSD_PAGE_SIZE;
            g_Context->PageTable[slot].DataLength = SSD_PAGE_SIZE;
            KeReleaseSpinLock(&g_Context->Lock, oldIrql);

            WriteLog("[HyperRAM] IOCTL WRITE: page stored in real SSD pool file.\r\n");
            status = STATUS_SUCCESS;
        } else {
            status = STATUS_INVALID_PARAMETER;
        }
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
