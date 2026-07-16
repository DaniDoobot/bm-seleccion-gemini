"""Script to test local or remote WebSocket handshake and initial greetings for both scenarios."""
import os
import asyncio
import json
import logging
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import websockets

# Set logging to see connections
logging.basicConfig(level=logging.INFO)

from app.main import app

client = TestClient(app)

async def test_handshake(scenario_id: str):
    base_url = os.environ.get("WS_BASE_URL", "").strip()
    
    print(f"\n=============================================================")
    if base_url:
        print(f"Testing remote WebSocket handshake for: {scenario_id}")
        print(f"Target URL: {base_url}?scenario={scenario_id}")
    else:
        print(f"Testing local WebSocket handshake for: {scenario_id}")
    print(f"=============================================================")
    
    start_payload = {
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
    }
    
    connected_payload = {
        "event": "connected",
        "protocol": "Call",
        "version": "1.0.0"
    }

    if base_url:
        # Remote mode using real websockets library
        target_ws_url = f"{base_url}?scenario={scenario_id}"
        try:
            async with websockets.connect(target_ws_url) as ws:
                # 1. Send Twilio-like 'connected' and 'start' event
                await ws.send(json.dumps(connected_payload))
                await ws.send(json.dumps(start_payload))
                
                print("Connected to remote WebSocket & sent start handshake.")
                print("Waiting for initial Gemini greeting (remote audio streaming)...")
                
                message_count = 0
                while message_count < 15:
                    raw_msg = await ws.recv()
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
                
                # Close connection
                await ws.close()
                print("WebSocket session closed cleanly.")
                
        except websockets.exceptions.ConnectionClosed as exc:
            print(f"WebSocket disconnected with code: {exc.code}, reason: {exc.reason}")
        except Exception as exc:
            print(f"Error during remote handshake: {exc}")
    else:
        # Local mode using TestClient
        try:
            with client.websocket_connect(f"/voice/stream?scenario={scenario_id}") as websocket:
                websocket.send_json(connected_payload)
                websocket.send_json(start_payload)
                
                print("Connected to local WebSocket & sent start handshake.")
                print("Waiting for initial Gemini greeting...")
                
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
                    
                websocket.close()
                print("WebSocket session closed cleanly.")
                
        except WebSocketDisconnect as exc:
            print(f"WebSocket disconnected with code: {exc.code}")
        except Exception as exc:
            print(f"Error during local handshake: {exc}")

async def main():
    # Detect target scenario if any specific requested, else run both and the invalid scenario check
    scenarios = ["seleccion_1", "seleccion_2", "escenario_inexistente"]
    for sc in scenarios:
        await test_handshake(sc)

if __name__ == "__main__":
    asyncio.run(main())
