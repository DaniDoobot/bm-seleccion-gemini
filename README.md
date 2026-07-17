# bm-seleccion-gemini

**Boston Medical Group — Candidate Selection Voice Service**

Servicio de voz en tiempo real para simulaciones de selección de candidatos, construido sobre [Gemini Live](https://ai.google.dev/gemini-api/docs/live) (Gemini Flash) mediante streaming bidireccional de audio WebSocket.

---

## Objetivo del proyecto

Proporcionar la base técnica para dos simulaciones de voz orientadas a la selección de candidatos de Boston Medical Group:

| Escenario | Descripción |
|---|---|
| `seleccion_1` | Selección 1 — Paciente que exige hablar con el doctor inmediatamente |
| `seleccion_2` | Selección 2 — Paciente molesto por retraso en envío de medicación |

El servicio actúa como puente de audio entre **Twilio Media Streams** y **Gemini Live**, convirtiendo el audio en tiempo real entre los formatos G.711 µ-law (Twilio) y PCM lineal (Gemini).

---

## Qué se ha reutilizado de `bm-analysis-service`

`bm-analysis-service` es el proyecto de referencia del que se han extraído **únicamente** los siguientes elementos conceptuales:

| Elemento | Detalle |
|---|---|
| Modelo de Gemini | `models/gemini-3.1-flash-live-preview` |
| Voz | `Algieba` (español peninsular, adulta, natural) |
| `thinkingLevel` | `minimal` (reduce latencia) |
| Formato de audio entrada | µ-law G.711 8 kHz → PCM 16 kHz |
| Formato de audio salida | PCM 24 kHz → µ-law G.711 8 kHz |
| Funciones de conversión | `audioop.ulaw2lin`, `audioop.ratecv`, `audioop.lin2ulaw` |
| Parámetros VAD | `silenceDurationMs=130`, `prefixPaddingMs=120` |
| Sensibilidad VAD | `START_SENSITIVITY_LOW` / `END_SENSITIVITY_HIGH` |
| Gestión de turno | `TURN_INCLUDES_ONLY_ACTIVITY` |
| Barge-in | `START_OF_ACTIVITY_INTERRUPTS` + limpieza buffer Twilio |
| URL WebSocket Gemini | `wss://generativelanguage.googleapis.com/ws/...` |
| Reglas de voz | Tono, registro, pronunciación peninsular (solo parámetros vocales) |
| Framework | FastAPI + `websockets` + `pydantic-settings` |

**No se ha copiado** lógica de negocio, personajes, datos de pacientes, autenticación, base de datos, HubSpot, n8n, Twilio IVR, evaluaciones ni ninguna otra funcionalidad de `bm-analysis-service`.

---

## Estructura del proyecto

```
bm-seleccion-gemini/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory + CORS + routers
│   ├── config.py                # Settings (pydantic-settings) — env vars centralizadas
│   ├── version.txt              # Versión del servicio
│   ├── core/
│   │   ├── __init__.py
│   │   ├── audio.py             # Conversión µ-law ↔ PCM (sin deps de sesión)
│   │   └── gemini_session.py    # GeminiVoiceSession — WebSocket Gemini Live
│   ├── scenarios/
│   │   ├── __init__.py
│   │   ├── base.py              # ScenarioConfig (dataclass inmutable)
│   │   ├── registry.py          # Registro central de escenarios
│   │   ├── seleccion_1.py       # Config escenario 1 (Fase 1: placeholder)
│   │   └── seleccion_2.py       # Config escenario 2 (Fase 1: placeholder)
│   └── routers/
│       ├── __init__.py
│       ├── health.py            # GET /health
│       └── voice.py             # WebSocket /voice/stream
├── tests/
│   ├── __init__.py
│   ├── test_audio.py            # Conversión de audio (unitarios)
│   ├── test_config.py           # Carga y validación de configuración
│   ├── test_scenarios.py        # Registro y resolución de escenarios
│   └── test_health.py           # Endpoint /health
├── .env.example                 # Plantilla de variables de entorno (sin secretos)
├── .env.test                    # Variables de test (valores dummy)
├── .gitignore
├── pytest.ini
├── requirements.txt
└── README.md
```

---

## Requisitos

- **Python** 3.11 o superior (recomendado: **Python 3.12**)
  - En Python 3.13+, el módulo `audioop` fue eliminado de la stdlib. El paquete `audioop-lts` se instala automáticamente como reemplazo (ver `requirements.txt`).
- Acceso a **Gemini Live API** (clave de API de Google AI Studio)
- **Twilio** (opcional en Fase 1 — solo necesario para pruebas de audio reales)

---

## Instalación

```bash
# 1. Crear y activar entorno virtual
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 2. Instalar dependencias
pip install -r requirements.txt
```

---

## Configuración de variables de entorno

```bash
# Copiar la plantilla
cp .env.example .env

# Editar .env con tus valores
```

Variables obligatorias:

| Variable | Descripción |
|---|---|
| `GEMINI_API_KEY` | Clave de API de Google AI Studio |

Variables configurables (con valores por defecto validados):

| Variable | Defecto | Descripción |
|---|---|---|
| `GEMINI_MODEL` | `models/gemini-3.1-flash-live-preview` | Modelo de Gemini Live |
| `GEMINI_VOICE_NAME` | `Algieba` | Voz preconfigurada de Gemini |
| `GEMINI_THINKING_LEVEL` | `minimal` | Nivel de razonamiento interno |
| `VAD_SILENCE_DURATION_MS` | `130` | Silencio para fin de turno (ms) |
| `VAD_PREFIX_PADDING_MS` | `120` | Relleno de inicio de habla (ms) |
| `PORT` | `8000` | Puerto del servidor |
| `CORS_ORIGINS` | `*` | Orígenes CORS permitidos |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `PUBLIC_WS_BASE_URL` | | URL base pública de WebSocket para la integración con Twilio |
| `TWILIO_ACCOUNT_SID` | | Account SID de Twilio para grabación programática |
| `TWILIO_AUTH_TOKEN` | | Auth Token de Twilio |
| `N8N_WEBHOOK_URL` | | URL del webhook de n8n para enviar resultados de la simulación |

---

## Inicio local

```bash
# Modo desarrollo con recarga automática
uvicorn app.main:app --reload --port 8000

# O con el puerto definido en .env
uvicorn app.main:app --reload --port ${PORT:-8000}
```

El servidor estará disponible en:
- API: `http://localhost:8000`
- Documentación: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

---

## Endpoints disponibles (Fase 1)

| Método | Path | Descripción |
|---|---|---|
| `GET` | `/health` | Estado del servicio, modelo, voz y escenarios |
| `WebSocket` | `/voice/stream?scenario=<id>` | Puente Twilio ↔ Gemini Live |

### Health check

```bash
curl http://localhost:8000/health
```

Respuesta esperada:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "model": "models/gemini-3.1-flash-live-preview",
  "voice": "Algieba",
  "api_key_configured": true,
  "scenarios": [
    { "id": "seleccion_1",  "name": "Selección 1" },
    { "id": "seleccion_2", "name": "Selección 2" }
  ]
}
```

### WebSocket de voz

Twilio Media Streams conecta a:

```
wss://<host>/voice/stream?scenario=seleccion_1
```

o

```
wss://<host>/voice/stream?scenario=seleccion_2
```

---

## Ejecución de tests

### Tests unitarios (sin credenciales)

```bash
# Todos los tests unitarios
pytest

# Con salida detallada
pytest -v

# Solo tests de audio (conversión µ-law↔PCM + WAV)
pytest tests/test_audio.py tests/test_audio_wav.py -v

# Solo tests de health
pytest tests/test_health.py -v
```

Los tests unitarios no realizan llamadas reales a Gemini ni a Twilio.

### Prueba de conexión real con Gemini Live

> [!IMPORTANT]
> Este script **requiere** una `GEMINI_API_KEY` válida en el archivo `.env` y **consume cuota de API**.
> No se ejecuta automáticamente con `pytest`.

```powershell
# Windows
.\.venv\Scripts\python.exe scripts\test_gemini_live.py
```

```bash
# macOS / Linux
.venv/bin/python scripts/test_gemini_live.py
```

El script valida:
- Que la API key es válida y activa.
- Que el modelo `models/gemini-3.1-flash-live-preview` acepta la conexión.
- Que la voz `Algieba` y los parámetros VAD son aceptados.
- Que Gemini genera audio en respuesta a un turno de texto.
- Que la conversión PCM 24 kHz → µ-law 8 kHz funciona sin errores.
- Que la sesión se abre y cierra correctamente.

Genera dos archivos WAV en `tmp/audio-tests/` (ignorados por Git):

| Archivo | Descripción |
|---|---|
| `gemini_output_24khz.wav` | Audio original de Gemini a 24 kHz |
| `twilio_output_8khz.wav` | Audio simulando el pipeline de Twilio a 8 kHz |

---


## Flujo de audio (referencia técnica)

```
Twilio (llamada real)
  │  G.711 µ-law 8 kHz — base64
  ▼
/voice/stream (WebSocket)
  │  audioop.ulaw2lin → audioop.ratecv(8k→16k)
  ▼
Gemini Live WebSocket
  │  audio/pcm;rate=16000 — base64
  ▼
  │ (respuesta de Gemini)
  │  PCM 24 kHz — base64
  ▼
/voice/stream
  │  audioop.ratecv(24k→8k) → audioop.lin2ulaw
  ▼
Twilio
  │  G.711 µ-law 8 kHz — base64
```

**Barge-in**: cuando Gemini detecta que el usuario habla mientras el modelo responde, envía `serverContent.interrupted = true`. El router envía inmediatamente `{"event": "clear", "streamSid": ...}` a Twilio para vaciar el buffer de reproducción.

---

- [ ] Integración completa con Twilio IVR (llamadas entrantes, TwiML)
- [ ] Selección de escenario mediante flujo de llamada (n8n, HubSpot)
- [ ] Monitor de duración de llamada y límites de cuota
- [ ] Gestión post-call (n8n, Google Sheets, CRM)
- [ ] Grabación de llamadas
- [ ] Tests de integración end-to-end con audio real
- [x] Despliegue en Dokploy

---

## Despliegue en Dokploy

El backend está preparado para desplegarse de manera automatizada en Dokploy utilizando el `Dockerfile` del proyecto.

### Configuración del Servicio en Dokploy
1. **Build Type**: Selecciona **Dockerfile**.
2. **Puerto Interno**: Configura el puerto **8000** (o el valor que definas en la variable `PORT`).
3. **Dominio**: Configura tu dominio, por ejemplo `bmseleccion-backend.doobot.ai`.
   - **Nota**: El dominio debe estar configurado con soporte SSL/TLS activo para poder utilizar WebSocket seguro (`wss://`).
4. **Variables de Entorno**:
   - `GEMINI_API_KEY`: Tu clave privada de Google AI Studio (obligatoria).
   - `PORT`: Puerto de escucha del contenedor (por defecto `8000`).
   - `LOG_LEVEL`: Nivel de logs de producción, por ejemplo `INFO`.
5. **Proxy Timeout (Crítico)**:
   - Dado que las sesiones de roleplay duran varios minutos y fluyen flujos continuos de audio, asegúrate de configurar un timeout de conexión y lectura suficientemente alto en el balanceador de carga / proxy inverso de Dokploy (por ejemplo, **300 segundos** o más) para evitar desconexiones prematuras por parte del proxy.

### Endpoint de Health Check
Dokploy o cualquier monitor externo puede verificar la salud en:
`GET https://bmseleccion-backend.doobot.ai/health`

### Rutas de WebSocket en Producción
Para conectar con Twilio Media Streams, utiliza:
- Escenario 1: `wss://bmseleccion-backend.doobot.ai/voice/stream?scenario=seleccion_1`
- Escenario 2: `wss://bmseleccion-backend.doobot.ai/voice/stream?scenario=seleccion_2`

---

## Seguridad

- La API key de Gemini **nunca** debe commitearse al repositorio ni escribirse en archivos de configuración estáticos.
- El archivo `.env` está excluido por `.gitignore`.
- El endpoint `/health` no expone la API key ni la URL completa de Gemini.
- Los prompts de escenario no se exponen en la respuesta del health check.
