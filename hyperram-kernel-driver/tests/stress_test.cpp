#include <windows.h>
#include <iostream>
#include <vector>
#include <random>
#include <thread>
#include <atomic>

#include "../Driver_NVMe_IO.h"

std::atomic<bool> g_Running{true};
std::atomic<uint64_t> g_Operations{0};

void StressThread(HANDLE hDevice, int threadId) {
    std::random_device rd;
    std::mt19937_64 gen(rd() + threadId);
    std::uniform_int_distribution<ULONG64> dist(0, 1000000); // Test across a large range of pages
    std::uniform_int_distribution<int> opDist(0, 1);

    std::vector<BYTE> buffer(4096 + sizeof(HYPERRAM_PAGE_REQUEST));
    
    while (g_Running) {
        HYPERRAM_PAGE_REQUEST* req = reinterpret_cast<HYPERRAM_PAGE_REQUEST*>(buffer.data());
        req->PageId = dist(gen);
        req->QoSTag = 0;
        req->DataLengthBytes = 4096;
        
        DWORD bytesReturned = 0;
        if (opDist(gen) == 0) {
            // Read
            DeviceIoControl(
                hDevice,
                IOCTL_HYPERRAM_READ_PAGE,
                req, sizeof(HYPERRAM_PAGE_REQUEST),
                buffer.data(), 4096,
                &bytesReturned,
                NULL
            );
        } else {
            // Write
            // Fill with some random data to test compression somewhat realistically
            for (size_t i = sizeof(HYPERRAM_PAGE_REQUEST); i < buffer.size(); i++) {
                buffer[i] = static_cast<BYTE>(gen() % 256);
            }
            DeviceIoControl(
                hDevice,
                IOCTL_HYPERRAM_WRITE_PAGE,
                buffer.data(), (DWORD)buffer.size(),
                NULL, 0,
                &bytesReturned,
                NULL
            );
        }
        g_Operations++;
    }
}

int main() {
    HANDLE hDevice = CreateFileW(
        L"\\\\.\\HyperRAM",
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        NULL,
        OPEN_EXISTING,
        0,
        NULL
    );

    if (hDevice == INVALID_HANDLE_VALUE) {
        std::cerr << "Failed to open HyperRAM device. Error: " << GetLastError() << std::endl;
        std::cerr << "Ensure the driver is loaded and running." << std::endl;
        return 1;
    }

    std::cout << "Starting 24-hour Stress Test..." << std::endl;
    std::cout << "Press Ctrl+C to stop early." << std::endl;

    int numThreads = std::thread::hardware_concurrency();
    std::vector<std::thread> threads;
    for (int i = 0; i < numThreads; ++i) {
        threads.emplace_back(StressThread, hDevice, i);
    }

    // Run and print stats periodically
    while (g_Running) {
        Sleep(5000);
        
        HYPERRAM_STATS stats = {0};
        DWORD bytesReturned = 0;
        if (DeviceIoControl(hDevice, IOCTL_HYPERRAM_GET_STATS, NULL, 0, &stats, sizeof(stats), &bytesReturned, NULL)) {
            std::cout << "--- Status Update ---" << std::endl;
            std::cout << "Ops/sec: " << (g_Operations.exchange(0) / 5) << std::endl;
            std::cout << "RAM Cache Pages: " << stats.RamCachePages << " / " << stats.MaxRamCachePages << std::endl;
            std::cout << "Cache Hits: " << stats.CacheHits << " | Cache Misses: " << stats.CacheMisses << std::endl;
            std::cout << "NVMe Reads: " << stats.NvmeReads << " | NVMe Writes: " << stats.NvmeWrites << std::endl;
            std::cout << "---------------------" << std::endl;
            
            if (stats.RamCachePages > stats.MaxRamCachePages + 100) {
                std::cerr << "[!] WARNING: RamCachePages is wildly exceeding MaxRamCachePages! Leak detected!" << std::endl;
            }
        }
    }

    for (auto& t : threads) {
        t.join();
    }

    CloseHandle(hDevice);
    return 0;
}
