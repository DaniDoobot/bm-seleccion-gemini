"""Script to test local WebSocket handshake and initial greetings for both scenarios."""
import asyncio
import json
import logging
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# Set logging to see connections
logging.basicConfig(level=logging.INFO)

from app.main import app

client = TestClient(app)

async def test_handshake(scenario_id: str):
    print(f"\n=============================================================")
    print(f"Testing local WebSocket handshake for: {scenario_id}")
    print(f"=============================================================")
    
    # We use client.websocket_connect in a synchronous-looking way (as TestClient wraps it)
    try:
        with client.websocket_connect(f"/voice/stream?scenario={scenario_id}") as websocket:
            # 1. Send Twilio-like 'connected' and 'start' event
            websocket.send_json({
                "event": "connected",
                "protocol": "Call",
                "version": "1.0.0"
            })
            websocket.send_json({
                "event": "start",
                "sequenceNumber": "1",
                "start": {
                    "accountSid": "ACxxxx",
                    "callSid": "CAxxxx",
                    "streamSid": "MZxxxx",
                    "tracks": ["inbound"],
                    "customParameters": {}
                },
                "streamSid": "MZxxxx"
            })
            
            print("Connected to WebSocket router & sent start handshake.")
            print("Waiting for initial Gemini greeting (this connects to Gemini Live API)...")
            
            # 2. Wait for Gemini audio/setup messages
            # In voice.py, after setup_complete, it requests Gemini to state the initial message.
            # Gemini responds with audio media chunks.
            # We will read messages until we get a media event or text transcript if available,
            # or just confirm we receive data cleanly from the bridge.
            message_count = 0
            while message_count < 10:
                raw_msg = websocket.receive_text()
                msg = json.loads(raw_msg)
                event = msg.get("event")
                if event == "media":
                    print(f"Success! Received audio media chunk from Gemini for {scenario_id}.")
                    break
                elif event == "mark":
                    print("Received mark event.")
                else:
                    print(f"Received event: {event}")
                message_count += 1
                
            # Clean close
            websocket.close()
            print("WebSocket session closed cleanly.")
            
    except WebSocketDisconnect as exc:
        print(f"WebSocket disconnected with code: {exc.code}")
    except Exception as exc:
        print(f"Error during handshake: {exc}")

if __name__ == "__main__":
    # Test both scenarios sequentially
    asyncio.run(test_handshake("seleccion_1"))
    asyncio.run(test_handshake("seleccion_2"))
