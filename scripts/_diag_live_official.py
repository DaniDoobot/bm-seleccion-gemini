"""
Prueba WebSocket mínima oficial — completamente aislada de GeminiVoiceSession.

Objetivo: comprobar autenticación, endpoint y modelo sin ningún parámetro
adicional del proyecto (sin voz, VAD, thinkingConfig, etc.).

No modifica ningún componente del proyecto.
No imprime ni registra la clave en ningún caso.
"""
import asyncio
import io
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.pop("APP_ENV", None)
os.environ.pop("PYTEST_CURRENT_TEST", None)

import websockets
import websockets.exceptions
from dotenv import dotenv_values


def sep(char="─", width=62):
    return char * width


print(sep("═"))
print("  WebSocket mínimo oficial — Gemini Live")
print(sep("═"))

# ── Carga de clave ────────────────────────────────────────────────
key = dotenv_values(PROJECT_ROOT / ".env").get("GEMINI_API_KEY", "").strip()
if not key:
    print("  ✗ GEMINI_API_KEY no encontrada en .env")
    sys.exit(1)

# ── Construcción de URL con urlencode ─────────────────────────────
BASE_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta."
    "GenerativeService.BidiGenerateContent"
)
query = urlencode({"key": key})
ws_url = f"{BASE_URL}?{query}"

# Diagnóstico de la URL (sin mostrar la clave)
from urllib.parse import urlencode as _ue
key_raw_len = len(key)
key_enc = _ue({"key": key}).split("=", 1)[1]
key_enc_len = len(key_enc)

print(f"\n  URL base          : {BASE_URL}")
print(f"  Parámetro         : key")
print(f"  Longitud clave raw: {key_raw_len}")
print(f"  Longitud clave enc: {key_enc_len}")
print(f"  Cambia al encodif.: {'SÍ' if key_enc != key else 'No'}")
print(f"  Longitud URL total: {len(ws_url)}")
print(f"  Signos '?' en URL : {ws_url.count('?')}")


# ── Setup mínimo ──────────────────────────────────────────────────
SETUP_MESSAGE = {
    "setup": {
        "model": "models/gemini-3.1-flash-live-preview",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
        },
        "systemInstruction": {
            "parts": [
                {
                    "text": "Responde exclusivamente en español."
                }
            ]
        }
    }
}


async def run():
    print(f"\n{sep()}")
    print("  Abriendo conexión WebSocket...")
    print(sep())

    try:
        ws = await websockets.connect(ws_url)
        print("  ✓ Conexión WebSocket establecida")
    except websockets.exceptions.WebSocketException as e:
        print(f"  ✗ Error al conectar: {type(e).__name__}: {e}")
        return
    except Exception as e:
        print(f"  ✗ Error inesperado al conectar: {type(e).__name__}: {e}")
        return

    # Enviar setup mínimo
    try:
        await ws.send(json.dumps(SETUP_MESSAGE))
        print("  ✓ Setup mínimo enviado")
        print(f"    Payload: {json.dumps(SETUP_MESSAGE, ensure_ascii=False)}")
    except Exception as e:
        print(f"  ✗ Error enviando setup: {e}")
        await ws.close()
        return

    # Esperar primera respuesta (máx. 15 s)
    print(f"\n{sep()}")
    print("  Esperando respuesta de Gemini (timeout 15s)...")
    print(sep())

    setup_complete = False
    try:
        async with asyncio.timeout(15):
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except Exception:
                    print(f"  ⚠ Mensaje no JSON: {raw[:200]}")
                    continue

                # Imprimir mensaje completo (sin la clave — que no debería estar en el payload)
                print(f"  Respuesta recibida:")
                print(f"    {json.dumps(data, ensure_ascii=False, indent=4)}")

                if "setupComplete" in data:
                    setup_complete = True
                    print(f"\n  ✓ setupComplete RECIBIDO")
                    break
                elif "error" in data:
                    print(f"\n  ✗ Error de Gemini en payload: {data['error']}")
                    break
                else:
                    print(f"  → Mensaje desconocido, esperando siguiente...")

    except TimeoutError:
        print("  ✗ Timeout: no se recibió respuesta en 15s")
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"  ✗ Conexión cerrada por Gemini: {e}")
    except Exception as e:
        print(f"  ✗ Error en el bucle de recepción: {type(e).__name__}: {e}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass

    print(f"\n{sep()}")
    print(f"  setupComplete recibido : {'✓ SÍ' if setup_complete else '✗ NO'}")
    print(sep("═"))
    return setup_complete


if __name__ == "__main__":
    result = asyncio.run(run())
    sys.exit(0 if result else 1)
