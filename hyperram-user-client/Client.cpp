#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <iostream>
#include <vector>
#include <chrono>
#include <iomanip>
#include <string>
#include <sstream>

#pragma comment(lib, "Ws2_32.lib")

using namespace std;

#define PAGE_SIZE 4096
#define MAX_PAGES 1000 // ~4MB of data
#define UDP_PORT 8001

class Timer {
    chrono::time_point<chrono::high_resolution_clock> start_time;
public:
    void start() { start_time = chrono::high_resolution_clock::now(); }
    double elapsed_us() {
        auto end_time = chrono::high_resolution_clock::now();
        return static_cast<double>(chrono::duration_cast<chrono::microseconds>(end_time - start_time).count());
    }
};

SOCKET udpSocket;
sockaddr_in serverAddr;

bool SetupUDP() {
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) {
        cerr << "[ERROR] WSAStartup failed" << endl;
        return false;
    }
    udpSocket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (udpSocket == INVALID_SOCKET) {
        cerr << "[ERROR] UDP socket creation failed" << endl;
        WSACleanup();
        return false;
    }
    
    serverAddr.sin_family = AF_INET;
    serverAddr.sin_port = htons(UDP_PORT);
    inet_pton(AF_INET, "127.0.0.1", &serverAddr.sin_addr);
    return true;
}

void SendTelemetry(int hits, int misses, double avg_latency, int ssd_reads, int ssd_writes) {
    // We send a JSON string that the Python daemon can parse
    stringstream ss;
    ss << "{"
       << "\"hits\":" << hits << ","
       << "\"misses\":" << misses << ","
       << "\"latency_ns\":" << (avg_latency * 1000.0) << ","
       << "\"ssd_reads\":" << ssd_reads << ","
       << "\"ssd_writes\":" << ssd_writes
       << "}";
       
    string payload = ss.str();
    sendto(udpSocket, payload.c_str(), static_cast<int>(payload.length()), 0, (sockaddr*)&serverAddr, sizeof(serverAddr));
}

int main() {
    cout << "==========================================" << endl;
    cout << "  HyperRAM V3 C++ Telemetry Client        " << endl;
    cout << "==========================================\n" << endl;

    if (!SetupUDP()) {
        cerr << "[ERROR] UDP setup failed, telemetry disabled." << endl;
    } else {
        cout << "[INFO] UDP Socket ready to broadcast on port " << UDP_PORT << "..." << endl;
    }

    cout << "[INFO] Opening connection to \\\\.\\HyperRAM..." << endl;
    HANDLE hDevice = CreateFileW(L"\\\\.\\HyperRAM", GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);

    if (hDevice == INVALID_HANDLE_VALUE) {
        cout << "[ERROR] Failed to open driver handle! Error: " << GetLastError() << endl;
        return 1;
    }
    cout << "[SUCCESS] Connected to HyperRAM Kernel Engine!\n" << endl;

    vector<uint8_t> buffer(PAGE_SIZE, 'A');
    DWORD bytesReturned = 0;
    Timer timer;
    
    int total_ssd_writes = 0;
    int total_ssd_reads = 0;

    cout << "[INFO] Starting continuous AI/Gaming workload simulation..." << endl;
    cout << "[INFO] Blasting real-time metrics to React UI..." << endl;

    while (true) {
        int cache_misses = 0;
        int cache_hits = 0;
        double total_time_us = 0;

        // 1. Simulate AI sequential reads (tensor streaming) -> Triggers Prefetching
        for (int i = 0; i < MAX_PAGES; i++) {
            LARGE_INTEGER offset;
            offset.QuadPart = (LONGLONG)i * PAGE_SIZE;
            SetFilePointerEx(hDevice, offset, NULL, FILE_BEGIN);

            // Periodically write to simulate spilling weights
            if (i % 200 == 0) {
                WriteFile(hDevice, buffer.data(), PAGE_SIZE, &bytesReturned, NULL);
                total_ssd_writes++;
            } else {
                timer.start();
                ReadFile(hDevice, buffer.data(), PAGE_SIZE, &bytesReturned, NULL);
                double us = timer.elapsed_us();
                
                if (us > 50) { 
                    cache_misses++;
                    total_ssd_reads++;
                } else {
                    cache_hits++;
                }
                total_time_us += us;
            }
        }

        // Calculate metrics
        double avg_lat = total_time_us / (cache_hits + cache_misses);
        
        // Send to Python Daemon over UDP
        SendTelemetry(cache_hits, cache_misses, avg_lat, total_ssd_reads, total_ssd_writes);
        
        cout << "\r[WORKLOAD] Hits: " << cache_hits << " | Misses: " << cache_misses 
             << " | Avg Latency: " << fixed << setprecision(1) << avg_lat << " us" << flush;

        Sleep(100); // Send updates every 100ms
    }

    CloseHandle(hDevice);
    if (udpSocket != INVALID_SOCKET) closesocket(udpSocket);
    WSACleanup();
    return 0;
}
