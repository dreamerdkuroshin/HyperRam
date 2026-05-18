import asyncio
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import psutil
from core import HyperRAMEngine

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the Engine (16GB Virtual RAM pool)
# In a real environment, this would be a singleton that handles OS-level hooks.
engine = HyperRAMEngine(pool_size_gb=16)

# Background task to simulate workload
async def simulate_workload():
    import random
    while True:
        # Simulate an application writing/reading pages
        page = random.randint(0, 100000)
        action = random.choice(["read", "write"])
        if action == "write":
            engine.write_page(page, b"A" * 4096)
        else:
            engine.read_page(page)
        
        await asyncio.sleep(0.005) # Super fast simulation

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(simulate_workload())

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Gather metrics
            sys_mem = psutil.virtual_memory()
            hyper_metrics = engine.get_metrics()
            
            payload = {
                "physical_ram_total_gb": sys_mem.total / (1024**3),
                "physical_ram_used_gb": sys_mem.used / (1024**3),
                "physical_ram_percent": sys_mem.percent,
                "hyperram_used_mb": hyper_metrics["ram_used_mb"] + hyper_metrics["ssd_used_mb"],
                "hyperram_hit_rate": hyper_metrics["hit_rate_percent"],
                "hyperram_compression": hyper_metrics["compression_ratio"],
                "hyperram_effective_latency": hyper_metrics["effective_latency_ns"],
                "ssd_writes": hyper_metrics["ssd_writes"],
                "ssd_reads": hyper_metrics["ssd_reads"]
            }
            
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(0.5) # update twice a second
            
    except WebSocketDisconnect:
        print("Client disconnected")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
