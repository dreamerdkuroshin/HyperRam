import asyncio
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import psutil
import random
from core import HyperRAMEngine, QoSTag

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = HyperRAMEngine(pool_size_gb=16)

async def simulate_gaming_workload():
    tags = [QoSTag.PHYSICS, QoSTag.STATE, QoSTag.TEXTURE, QoSTag.SHADER, QoSTag.AI]
    # Weighted random choices: Textures are read heavy, Physics is write heavy, Shaders are write-once
    while True:
        tag = random.choices(
            tags, 
            weights=[30, 20, 40, 5, 5], 
            k=1
        )[0]
        
        # Keep physics in a specific page range
        if tag == QoSTag.PHYSICS:
            page = random.randint(0, 100)
            engine.write_page(page, b"P" * 4096, tag)
        elif tag == QoSTag.TEXTURE:
            page = random.randint(10000, 50000)
            engine.read_page(page)
            engine.write_page(page, b"T" * 4096, tag) # Simulate loading texture
        elif tag == QoSTag.SHADER:
            page = random.randint(50000, 60000)
            engine.write_page(page, b"S" * 4096, tag)
        elif tag == QoSTag.AI:
            page = random.randint(60000, 70000)
            action = random.choice(["read", "write"])
            if action == "write":
                engine.write_page(page, b"A" * 4096, tag)
            else:
                engine.read_page(page)
        else:
            page = random.randint(100, 1000)
            engine.write_page(page, b"S" * 4096, tag)
            
        await asyncio.sleep(0.001)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(simulate_gaming_workload())

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
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
                "ssd_reads": hyper_metrics["ssd_reads"],
                "pinned_pages": hyper_metrics["pinned_pages"],
                "qos_traffic": hyper_metrics["qos_traffic"]
            }
            
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(0.2)
            
    except WebSocketDisconnect:
        print("Client disconnected")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
