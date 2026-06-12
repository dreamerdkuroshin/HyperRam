#include <iostream>
#include <vector>
#include <unordered_map>
#include <thread>
#include <chrono>
#include <iomanip>

using namespace std;

// ---------------------------------------------------------
// 1. Windows Kernel Mocks (Simulating the OS)
// ---------------------------------------------------------

enum class IRP_MAJOR_FUNCTION {
    IRP_MJ_READ,
    IRP_MJ_WRITE
};

// Fake I/O Request Packet (IRP) sent by the Windows OS
struct IRP {
    IRP_MAJOR_FUNCTION MajorFunction;
    uint64_t ByteOffset;     // Where in pagefile.sys
    uint32_t Length;         // How much data
    void* Buffer;            // The actual memory payload
};

// ---------------------------------------------------------
// 2. HyperRAM Storage Filter Driver (The Core Logic)
// ---------------------------------------------------------

class HyperRAMFilter {
private:
    uint32_t PAGE_SIZE = 4096;
    unordered_map<uint64_t, vector<uint8_t>> ssd_pool; // Simulating SSD Storage
    unordered_map<uint64_t, vector<uint8_t>> ram_cache; // Simulating Hot RAM
    
    int cache_hits = 0;
    int cache_misses = 0;

    // Tau-based predictor state
    chrono::time_point<chrono::high_resolution_clock> last_access_time;
    bool has_last_access = false;
    double inter_arrival_tau = 10.0; // characteristic time in ms
    uint64_t last_page_id = 0;
    int last_stride = 1;
    int stride_confidence = 0;

    // Mock LZ4 Compression (Just shrinks data artificially for simulation)
    vector<uint8_t> MockLZ4Compress(const vector<uint8_t>& data) {
        // Assume 50% compression ratio for AI Tensors
        vector<uint8_t> compressed(data.begin(), data.begin() + (data.size() / 2));
        return compressed;
    }

    vector<uint8_t> MockLZ4Decompress(const vector<uint8_t>& data) {
        vector<uint8_t> decompressed = data;
        decompressed.resize(PAGE_SIZE, 0); // Restore to full 4096 bytes
        return decompressed;
    }

public:
    void PrintMetrics() {
        int total = cache_hits + cache_misses;
        double hit_rate = total > 0 ? ((double)cache_hits / total) * 100.0 : 0.0;
        
        cout << "\n[HyperRAM Kernel Filter] --- Real-Time Metrics ---" << endl;
        cout << "RAM Cache Hits:   " << cache_hits << " (Nanosecond Latency)" << endl;
        cout << "SSD Cache Misses: " << cache_misses << " (Microsecond Latency)" << endl;
        cout << "Hit Rate:         " << fixed << setprecision(2) << hit_rate << "%" << endl;
        cout << "--------------------------------------------------\n" << endl;
    }

    // This function intercepts ALL reads/writes to pagefile.sys
    void InterceptIRP(IRP& irp) {
        uint64_t page_id = irp.ByteOffset / PAGE_SIZE;

        if (irp.MajorFunction == IRP_MAJOR_FUNCTION::IRP_MJ_WRITE) {
            // Write (Eviction from RAM -> SSD)
            vector<uint8_t> raw_data((uint8_t*)irp.Buffer, ((uint8_t*)irp.Buffer) + irp.Length);
            vector<uint8_t> compressed = MockLZ4Compress(raw_data);
            ssd_pool[page_id] = compressed;
        } 
        else if (irp.MajorFunction == IRP_MAJOR_FUNCTION::IRP_MJ_READ) {
            // Read (Page Fault: App needs memory from SSD)
            
            // 1. Check RAM Cache First
            if (ram_cache.find(page_id) != ram_cache.end()) {
                cache_hits++;
                // Copy data to the IRP buffer
                memcpy(irp.Buffer, ram_cache[page_id].data(), irp.Length);
            } 
            else {
                // 2. Cache Miss - Fetch from SSD
                cache_misses++;
                if (ssd_pool.find(page_id) != ssd_pool.end()) {
                    vector<uint8_t> decompressed = MockLZ4Decompress(ssd_pool[page_id]);
                    memcpy(irp.Buffer, decompressed.data(), irp.Length);
                    
                    // Promote to RAM cache
                    ram_cache[page_id] = decompressed;
                }
            }

            // 3. TAU-BASED PREDICTIVE PREFETCHING LOGIC
            auto now = chrono::high_resolution_clock::now();
            if (has_last_access) {
                double delta_t_ms = chrono::duration<double, milli>(now - last_access_time).count();
                inter_arrival_tau = 0.85 * inter_arrival_tau + 0.15 * delta_t_ms;
            }
            has_last_access = true;
            last_access_time = now;

            int current_stride = (int)(page_id - last_page_id);
            if (current_stride == last_stride) {
                stride_confidence = min(8, stride_confidence + 1);
            } else {
                stride_confidence = max(0, stride_confidence - 2);
                last_stride = current_stride;
            }
            last_page_id = page_id;

            // Adaptive prefetch depth D based on tau and stride confidence
            int prefetch_depth = 0;
            if (stride_confidence >= 3 && last_stride != 0) {
                // Highly sequential/predictable stride. Scale depth inversely with tau (smaller tau = higher speed)
                prefetch_depth = min(8, max(1, (int)(12.0 / (inter_arrival_tau + 0.1))));
            } else {
                // Workload is random/switching or idle
                prefetch_depth = 0;
            }

            // Perform adaptive prefetching along the detected stride direction
            for (int d = 1; d <= prefetch_depth; d++) {
                uint64_t next_page = page_id + d * last_stride;
                if (ssd_pool.find(next_page) != ssd_pool.end() && ram_cache.find(next_page) == ram_cache.end()) {
                    ram_cache[next_page] = MockLZ4Decompress(ssd_pool[next_page]);
                }
            }
        }
    }
};

// ---------------------------------------------------------
// 3. Ollama 8B Simulation Workload
// ---------------------------------------------------------

int main() {
    cout << "=== HyperRAM Kernel Storage Filter Simulation ===" << endl;
    cout << "Initializing Fake OS Environment..." << endl;
    
    HyperRAMFilter filter;
    
    const int NUM_PAGES = 1000;
    const int PAGE_SIZE = 4096;
    vector<uint8_t> fake_buffer(PAGE_SIZE, 1); // 4KB of fake data

    cout << "\n[Windows OS] Windows Memory is full! Paging Ollama 8B memory out to SSD..." << endl;
    for (int i = 0; i < NUM_PAGES; i++) {
        IRP write_irp;
        write_irp.MajorFunction = IRP_MAJOR_FUNCTION::IRP_MJ_WRITE;
        write_irp.ByteOffset = i * PAGE_SIZE;
        write_irp.Length = PAGE_SIZE;
        write_irp.Buffer = fake_buffer.data();
        
        filter.InterceptIRP(write_irp);
    }
    cout << "[HyperRAM Filter] Successfully intercepted and compressed 1000 pages to SSD." << endl;

    cout << "\n[Ollama] Generating Token (Sequential Tensor Stream)..." << endl;
    
    // Simulate Ollama reading memory sequentially (Tensor Streaming)
    for (int i = 0; i < NUM_PAGES; i++) {
        IRP read_irp;
        read_irp.MajorFunction = IRP_MAJOR_FUNCTION::IRP_MJ_READ;
        read_irp.ByteOffset = i * PAGE_SIZE;
        read_irp.Length = PAGE_SIZE;
        read_irp.Buffer = fake_buffer.data();
        
        filter.InterceptIRP(read_irp);
        
        // Add artificial delay to simulate CPU processing time between memory requests
        this_thread::sleep_for(chrono::milliseconds(2));
    }

    cout << "[Ollama] Token Generation Complete." << endl;
    
    // Print the final result to prove prefetching worked
    filter.PrintMetrics();

    cout << "Press Enter to exit..." << endl;
    cin.get();
    return 0;
}
