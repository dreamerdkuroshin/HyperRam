# HyperRAM - Software-Defined Memory Engine

## Goal
Build "HyperRAM", an advanced software-defined memory engine that converts SSD storage into high-capacity virtual RAM. The system will feature intelligent caching, LZ4/ZSTD compression, and a predictive memory manager. The goal is to allow memory-heavy workloads (like AI tensor streaming and large GGUF models) to run on systems with limited physical RAM by seamlessly paging to high-speed NVMe/SATA SSDs.

> [!WARNING]
> **Hardware Limitations & Latency Bridging:** An SSD's native hardware latency is in microseconds, while physical RAM (DDR3, DDR4, DDR5) operates in nanoseconds. While we cannot change the physical speed of the SSD, **HyperRAM bridges this gap using predictive prefetching and intelligent caching.** By predicting which memory pages the application will need and pre-loading them into physical RAM before they are requested, the *effective* perceived latency is converted from microseconds back down to nanoseconds for the application. HyperRAM is designed to augment systems with any memory generation, including DDR3, DDR4, and DDR5, preventing OOM crashes and supercharging effective capacity.

## User Review Required
Please review the proposed architecture and technology stack below. 

Since you requested avoiding unsafe kernel manipulation in early versions, HyperRAM will be built as a **User-Space Memory Allocator & Daemon**. It cannot magically give the Windows Task Manager "more RAM" transparently without a kernel driver, but it *will* provide an API (C/C++/Rust/Python) that AI applications (like llama.cpp) can use to allocate memory from the SSD pool seamlessly.

Are you okay with using **Rust** for the core engine and **Tauri (React/TypeScript)** for the Performance Dashboard? Rust is highly recommended here due to its zero-cost abstractions, fearless concurrency, and memory safety, which are critical for building a custom memory manager.

## Proposed Architecture

### 1. Core Memory Engine (`hyperram-core` - Rust)
* **Virtual RAM Pool:** Utilizes `memmap2` (Rust crate mapping to Windows `CreateFileMapping` / Linux `mmap`) to allocate large blocks of SSD storage.
* **Compression Engine:** Integrates `lz4_flex` or `zstd` to compress inactive memory pages before writing them to the SSD, multiplying effective capacity.
* **Real-Time Memory Manager:** A custom allocator that tracks hot/cold pages. Hot pages stay in physical RAM, cold pages are compressed and flushed to the SSD.
* **Async Streaming (IOCP/io_uring):** Uses Rust's `tokio` asynchronous runtime to handle parallel read/write queues to the SSD, maximizing NVMe queue depth.

### 2. Client API Wrapper (`hyperram-api`)
* Exposes C-bindings (FFI) so that C++ applications (like AI model runners) can allocate memory using `hyperram_malloc(size)` and `hyperram_free(ptr)`.
* Potential integration points for Python (e.g., overriding tensor allocations).

### 3. Performance Dashboard (`hyperram-ui` - Tauri + React)
* A beautiful, dynamic, glassmorphic UI built with React and Tailwind CSS.
* Communicates with the Rust backend daemon to display real-time metrics:
  * Physical RAM Usage vs SSD Virtual RAM Usage
  * SSD Read/Write Bandwidth (MB/s)
  * Compression Ratio (e.g., 2.5x)
  * Cache Hit Rate (%)
* Allows the user to configure the size of the SSD Virtual RAM pool (e.g., allocate 16GB, 32GB, 64GB).

## Implementation Steps

1. **Initialize Project Structure:**
   * Create a Cargo workspace containing the core engine and the Tauri application.
2. **Build the Storage Pool (SSD Layer):**
   * Implement sparse file creation and memory mapping on the target drive.
3. **Develop the Paging & Compression System:**
   * Create a page table to map virtual addresses to physical RAM or SSD offsets.
   * Implement LZ4 compression on page eviction.
4. **Build the C-API:**
   * Expose functions to allocate/free/read/write to the HyperRAM pool.
5. **Develop the Performance Dashboard:**
   * Build the UI with modern styling to wow the user.
   * Hook up real-time telemetry from the Rust core to the frontend.

## Verification Plan
* **Automated Tests:** Unit tests in Rust to ensure data written to the HyperRAM allocator can be read back identically, even after being evicted to SSD and compressed.
* **Manual Verification:** Create a mock workload (allocating 16GB of data on a system configured with a 24GB HyperRAM pool) and observe the Performance Dashboard to verify compression, SSD I/O, and cache hits.
