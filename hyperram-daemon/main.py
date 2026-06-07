import asyncio
import json
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import psutil
from core import HyperRAMEngine

engine = HyperRAMEngine(pool_size_gb=2)

# Global state to hold real metrics from C++ client
import threading
_metrics_lock = threading.Lock()
live_metrics = {
    "hits": 0,
    "misses": 0,
    "latency_ns": 0,
    "ssd_reads": 0,
    "ssd_writes": 0
}

_udp_transport = None

class TelemetryProtocol(asyncio.DatagramProtocol):
    def datagram_received(self, data, addr):
        try:
            payload = json.loads(data.decode())
            with _metrics_lock:
                live_metrics["hits"] = payload.get("hits", live_metrics["hits"])
                live_metrics["misses"] = payload.get("misses", live_metrics["misses"])
                live_metrics["latency_ns"] = payload.get("latency_ns", live_metrics["latency_ns"])
                live_metrics["ssd_reads"] = payload.get("ssd_reads", live_metrics["ssd_reads"])
                live_metrics["ssd_writes"] = payload.get("ssd_writes", live_metrics["ssd_writes"])
        except Exception as e:
            print(f"UDP Parse Error: {e}")

def set_windows_pagefile(size_gb: int):
    size_mb = size_gb * 1024
    script = f"""
    $sys = Get-CimInstance Win32_ComputerSystem
    if ($sys.AutomaticManagedPagefile) {{
        Set-CimInstance -InputObject $sys -Property @{{AutomaticManagedPagefile = $false}}
    }}
    $pagefile = Get-CimInstance Win32_PageFileSetting
    if ($pagefile) {{
        $pagefile | Set-CimInstance -Property @{{InitialSize = {size_mb}; MaximumSize = {size_mb}}}
    }} else {{
        New-CimInstance -ClassName Win32_PageFileSetting -Property @{{Name = 'C:\\pagefile.sys'; InitialSize = {size_mb}; MaximumSize = {size_mb}}}
    }}
    """
    try:
        encoded_script = script.encode('utf-16le')
        import base64
        b64 = base64.b64encode(encoded_script).decode('utf-8')
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Start-Process powershell -ArgumentList '-NoProfile -EncodedCommand {b64}' -Verb RunAs -WindowStyle Hidden"], capture_output=True, text=True)
    except Exception as e:
        print(f"Elevation failed: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _udp_transport
    loop = asyncio.get_running_loop()
    # FIX Bug-2: Use SO_REUSEADDR so the daemon can restart without WinError 10048
    import socket as _socket
    udp_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    udp_sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    udp_sock.setblocking(False)
    try:
        udp_sock.bind(('127.0.0.1', 8001))
    except OSError as e:
        print(f"[WARN] UDP bind failed ({e}); telemetry from C++ clients disabled.")
        udp_sock.close()
        udp_sock = None

    if udp_sock is not None:
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: TelemetryProtocol(),
            sock=udp_sock
        )
        _udp_transport = transport
        print("UDP Telemetry Listener running on 127.0.0.1:8001")
    else:
        transport = None
    yield
    if transport is not None:
        transport.close()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _closed = False

    async def listen():
        nonlocal _closed
        try:
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                if msg.get("action") == "resize":
                    try:
                        new_size = int(msg.get("size_gb", 2))
                        engine.resize_pool(new_size)
                        set_windows_pagefile(new_size)
                    except Exception as e:
                        print(f"Resize Error: {e}")
        except (WebSocketDisconnect, Exception):
            # FIX Bug-1: Mark connection closed so the sender loop exits cleanly
            _closed = True

    listen_task = asyncio.create_task(listen())

    try:
        while not _closed:
            sys_mem = psutil.virtual_memory()
            eng_metrics = engine.get_metrics()

            with _metrics_lock:
                total_reqs = live_metrics["hits"] + live_metrics["misses"]
                m_hits     = live_metrics["hits"]
                m_misses   = live_metrics["misses"]
                m_latency  = live_metrics["latency_ns"]
                m_ssd_writes = live_metrics["ssd_writes"]
                m_ssd_reads  = live_metrics["ssd_reads"]

            hit_rate = (m_hits / total_reqs * 100) if total_reqs > 0 else 0

            payload = {
                "physical_ram_total_gb": sys_mem.total / (1024**3),
                "physical_ram_used_gb": sys_mem.used / (1024**3),
                "physical_ram_percent": sys_mem.percent,
                "hyperram_used_mb": eng_metrics["ram_used_mb"],
                "hyperram_pool_gb": engine.pool_size_gb,
                "hyperram_hit_rate": eng_metrics["hit_rate_percent"] if eng_metrics["hit_rate_percent"] < 100 else hit_rate,
                "hyperram_compression": eng_metrics["compression_ratio"],
                "hyperram_effective_latency": m_latency if m_latency > 0 else eng_metrics["effective_latency_ns"],
                "ssd_writes": m_ssd_writes + eng_metrics["ssd_writes"],
                "ssd_reads": m_ssd_reads + eng_metrics["ssd_reads"],
                "pinned_pages": eng_metrics["pinned_pages"],
                "qos_traffic": eng_metrics["qos_traffic"]
            }

            try:
                # FIX Bug-1: Catch ALL exceptions (incl. RuntimeError after close)
                await websocket.send_text(json.dumps(payload))
            except Exception:
                break

            await asyncio.sleep(0.2)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _closed = True
        listen_task.cancel()
        try:
            await listen_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
