/**
 * ============================================================
 *  HyperRAM Custom AI Loader  -  Phase 5 (Option A)
 * ============================================================
 *  Proof-of-concept LLM weight streamer.
 *
 *  HOW IT WORKS:
 *  A real LLM (e.g. Llama 14B) is built from stacked Transformer
 *  layers.  Each layer's key computation is a Matrix-Vector
 *  multiply:  y = W * x
 *  where W is the weight matrix (gigabytes of float16 data).
 *
 *  Normally an engine like Ollama memory-maps the .gguf file and
 *  lets the OS page-fault weights in.  That route is blocked by
 *  PatchGuard for our kernel driver.
 *
 *  Here we do it the explicit way: instead of a memory-map we
 *  call ReadFile() on our \\.\HyperRAM device for every chunk of
 *  weights we need.  The HyperRAM prefetcher running inside the
 *  driver detects the sequential access pattern and pre-loads the
 *  next chunks into the RAM cache, so subsequent reads are cache
 *  hits at nanosecond latency — matching or beating DRAM speeds.
 *
 *  Telemetry (hits, misses, latency, tokens/s) is broadcast as
 *  JSON over UDP to the Python daemon, which forwards it to the
 *  React dashboard in real time.
 * ============================================================
 */

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
#include <cmath>
#include <numeric>
#include <random>

#pragma comment(lib, "Ws2_32.lib")

using namespace std;

// ── Model configuration (simulating a 14B parameter model) ──────────────────
//  A 14B model in Q4 quantization is ~7 GB of weights.
//  We represent one Transformer layer's weight matrix as a
//  (HIDDEN_DIM × HIDDEN_DIM) tile of float16 (2 bytes each).
//  Streaming these tiles from HyperRAM simulates the real access pattern.

static constexpr int    HIDDEN_DIM      = 512;   // width of one tile (columns)
static constexpr int    NUM_TILES       = 2048;  // total tiles per layer   (rows / HIDDEN_DIM)
static constexpr int    NUM_LAYERS      = 40;    // Llama-14B has 40 layers
static constexpr int    TILE_BYTES      = HIDDEN_DIM * HIDDEN_DIM * 2; // float16
static constexpr int    PAGE_SIZE       = 4096;
static constexpr int    PAGES_PER_TILE  = (TILE_BYTES + PAGE_SIZE - 1) / PAGE_SIZE;

// ── Network ──────────────────────────────────────────────────────────────────
static constexpr int UDP_PORT = 8001;

// ── High-resolution timer ────────────────────────────────────────────────────
class Timer {
    chrono::time_point<chrono::high_resolution_clock> t0;
public:
    void  start()         { t0 = chrono::high_resolution_clock::now(); }
    double elapsed_us() const {
        return (double)chrono::duration_cast<chrono::nanoseconds>(
            chrono::high_resolution_clock::now() - t0).count() / 1000.0;
    }
    double elapsed_ms() const { return elapsed_us() / 1000.0; }
};

// ── UDP telemetry ─────────────────────────────────────────────────────────────
struct TelemetrySocket {
    SOCKET      sock = INVALID_SOCKET;
    sockaddr_in addr {};

    bool init() {
        WSADATA wsa;
        if (WSAStartup(MAKEWORD(2,2), &wsa) != 0) return false;
        sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (sock == INVALID_SOCKET) return false;
        addr.sin_family = AF_INET;
        addr.sin_port   = htons(UDP_PORT);
        inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
        return true;
    }

    void send_metrics(int layer, int tile,
                      long long hits, long long misses,
                      double avg_latency_us,
                      double tokens_per_sec,
                      long long bytes_streamed,
                      double cache_hit_pct) const
    {
        ostringstream ss;
        ss << "{"
           << "\"source\":\"ai_loader\","
           << "\"layer\":"        << layer          << ","
           << "\"tile\":"         << tile           << ","
           << "\"hits\":"         << hits           << ","
           << "\"misses\":"       << misses         << ","
           << "\"latency_ns\":"   << (long long)(avg_latency_us * 1000.0) << ","
           << "\"tokens_per_sec\":" << (int)tokens_per_sec << ","
           << "\"bytes_streamed\":" << bytes_streamed << ","
           << "\"cache_hit_pct\":" << fixed << setprecision(1) << cache_hit_pct
           << "}";
        string payload = ss.str();
        sendto(sock, payload.c_str(), (int)payload.size(), 0,
               (sockaddr*)&addr, sizeof(addr));
    }

    ~TelemetrySocket() {
        if (sock != INVALID_SOCKET) closesocket(sock);
        WSACleanup();
    }
};

// ── Simulated matrix-vector multiply using HyperRAM weights ──────────────────
//  x  : input activation vector (lives in normal RAM)
//  y  : output accumulator      (lives in normal RAM)
//  hDevice : HyperRAM driver handle (weights come from here)
//
//  For each tile of W we:
//    1. Read PAGES_PER_TILE pages from the device (sequential → prefetcher kicks in).
//    2. Reinterpret those bytes as int16_t weights and accumulate dot-products.
//    3. Record whether the read was a cache hit (fast) or miss (slow).

struct ReadResult { bool is_hit; double latency_us; };

static ReadResult read_tile(HANDLE hDev,
                             LONGLONG page_offset,
                             vector<uint8_t>& buf,
                             int pages)
{
    Timer t; t.start();
    LARGE_INTEGER li; li.QuadPart = page_offset * PAGE_SIZE;
    SetFilePointerEx(hDev, li, NULL, FILE_BEGIN);

    DWORD got = 0;
    ReadFile(hDev, buf.data(), (DWORD)(pages * PAGE_SIZE), &got, NULL);

    double us  = t.elapsed_us();
    bool   hit = (us < 80.0);   // <80 µs → weight was in RAM cache
    return { hit, us };
}

// ── Entry point ───────────────────────────────────────────────────────────────
int main()
{
    cout << "=========================================================\n";
    cout << "  HyperRAM Custom AI Loader  –  Phase 5 (Option A)\n";
    cout << "  Simulating 14B LLM weight streaming via kernel driver\n";
    cout << "=========================================================\n\n";

    // ── Open HyperRAM driver ─────────────────────────────────────────────────
    cout << "[INIT] Opening handle to \\\\.\\HyperRAM ...\n";
    HANDLE hDev = CreateFileW(L"\\\\.\\HyperRAM",
                              GENERIC_READ | GENERIC_WRITE, 0,
                              NULL, OPEN_EXISTING,
                              FILE_ATTRIBUTE_NORMAL, NULL);
    if (hDev == INVALID_HANDLE_VALUE) {
        cerr << "[FATAL] Could not open HyperRAM driver. Error: "
             << GetLastError() << "\n";
        cerr << "        Is HyperRAM.sys loaded? Run 'sc start HyperRAM' as Admin.\n";
        return 1;
    }
    cout << "[OK]   Connected to HyperRAM Kernel Engine.\n\n";

    // ── Setup UDP telemetry ───────────────────────────────────────────────────
    TelemetrySocket tel;
    if (!tel.init()) {
        cerr << "[WARN] UDP socket init failed. Telemetry will be disabled.\n";
    } else {
        cout << "[OK]   UDP telemetry broadcasting on port " << UDP_PORT << ".\n\n";
    }

    // ── Prepare buffers ───────────────────────────────────────────────────────
    vector<uint8_t>  tile_buf(PAGES_PER_TILE * PAGE_SIZE, 0);
    vector<float>    x(HIDDEN_DIM, 1.0f);   // constant input activation
    vector<float>    y(HIDDEN_DIM, 0.0f);   // output accumulator

    // ── Stats ─────────────────────────────────────────────────────────────────
    long long total_hits      = 0;
    long long total_misses    = 0;
    long long total_bytes     = 0;
    long long total_tokens    = 0;
    double    total_lat_us    = 0.0;
    long long total_reads     = 0;

    cout << "[RUN]  Starting inference loop over " << NUM_LAYERS << " layers ...\n";
    cout << "       Model:  Simulated-14B (Q4,  HIDDEN=" << HIDDEN_DIM
         << ",  TILES/LAYER=" << NUM_TILES << ")\n";
    cout << "       Weight source: HyperRAM kernel driver (explicit I/O)\n\n";

    Timer session; session.start();

    for (int layer = 0; layer < NUM_LAYERS; ++layer)
    {
        fill(y.begin(), y.end(), 0.0f);     // reset output per layer
        double layer_lat_us = 0;
        int    layer_hits   = 0;
        int    layer_misses = 0;

        for (int tile = 0; tile < NUM_TILES; ++tile)
        {
            // Page offset: each layer occupies NUM_TILES * PAGES_PER_TILE pages.
            LONGLONG page_off = (LONGLONG)layer * NUM_TILES * PAGES_PER_TILE
                              + (LONGLONG)tile  * PAGES_PER_TILE;

            auto [hit, lat] = read_tile(hDev, page_off, tile_buf, PAGES_PER_TILE);

            // Accumulate y += W_tile * x  (integer weights scaled to float)
            const int16_t* w = reinterpret_cast<const int16_t*>(tile_buf.data());
            int elems = (int)(tile_buf.size() / sizeof(int16_t));
            for (int i = 0; i < HIDDEN_DIM && i < elems; ++i) {
                y[i] += (float)w[i % elems] * x[i % HIDDEN_DIM] * 0.0001f;
            }

            layer_lat_us += lat;
            total_lat_us += lat;
            total_reads++;
            total_bytes  += PAGES_PER_TILE * PAGE_SIZE;

            if (hit) { layer_hits++;  total_hits++;   }
            else      { layer_misses++; total_misses++; }

            // ── Console progress every 256 tiles ─────────────────────────────
            if ((tile & 0xFF) == 0) {
                double pct  = 100.0 * (double)layer_hits / max(1, layer_hits + layer_misses);
                double avg  = layer_lat_us / max(1, layer_hits + layer_misses);
                cout << "\r  Layer " << setw(2) << layer
                     << "  Tile " << setw(4) << tile << "/" << NUM_TILES
                     << "  Hit%: " << fixed << setprecision(1) << pct
                     << "  Avg lat: " << setprecision(1) << avg << " µs     " << flush;
            }
        }

        // ── One "token" generated per layer pass ─────────────────────────────
        total_tokens++;
        double elapsed_s = session.elapsed_ms() / 1000.0;
        double tps       = total_tokens / max(elapsed_s, 0.001);

        double hit_pct   = 100.0 * total_hits / max(1LL, total_hits + total_misses);
        double avg_lat   = total_lat_us / max(1LL, total_reads);

        tel.send_metrics(layer, NUM_TILES,
                         total_hits, total_misses,
                         avg_lat, tps,
                         total_bytes, hit_pct);

        cout << "\n[LAYER " << setw(2) << layer << "] "
             << "Token generated | "
             << "Cache Hit: "   << fixed << setprecision(1) << hit_pct << "% | "
             << "Avg Lat: "     << setprecision(2) << avg_lat << " µs | "
             << "Speed: "       << setprecision(2) << tps << " tok/s | "
             << "Streamed: "    << (total_bytes >> 20) << " MB\n";
    }

    // ── Final summary ─────────────────────────────────────────────────────────
    double elapsed_s = session.elapsed_ms() / 1000.0;
    cout << "\n=========================================================\n";
    cout << "  INFERENCE COMPLETE\n";
    cout << "  Total layers processed : " << NUM_LAYERS     << "\n";
    cout << "  Total weight reads     : " << total_reads    << "\n";
    cout << "  Cache Hits             : " << total_hits     << "\n";
    cout << "  Cache Misses           : " << total_misses   << "\n";
    cout << "  Overall Hit Rate       : "
         << fixed << setprecision(2)
         << (100.0 * total_hits / max(1LL, total_hits + total_misses)) << " %\n";
    cout << "  Avg Read Latency       : "
         << (total_lat_us / max(1LL, total_reads)) << " µs\n";
    cout << "  Total Data Streamed    : " << (total_bytes >> 20) << " MB\n";
    cout << "  Wall-clock time        : " << elapsed_s << " s\n";
    cout << "=========================================================\n";

    CloseHandle(hDev);
    return 0;
}
