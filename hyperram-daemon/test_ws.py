import asyncio, json, websockets

async def test():
    async with websockets.connect("ws://localhost:8000/ws") as ws:
        data = await asyncio.wait_for(ws.recv(), timeout=5)
        msg = json.loads(data)
        print(f"WebSocket OK - got {len(msg)} fields")
        for k, v in msg.items():
            print(f"  {k}: {v}")

asyncio.run(test())
