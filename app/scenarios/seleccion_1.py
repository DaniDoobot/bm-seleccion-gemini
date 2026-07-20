"""Scenario: seleccion_1 — Patient demanding doctor immediately.

Loads the full system instruction prompt from its independent markdown file.
Conserves vocal style rules and aggregates them with the scenario prompt.
Defines function declarations (tools) and handlers for candidate context saving.
"""
import logging
from pathlib import Path
from typing import Any, Callable, Dict
from app.scenarios.base import ScenarioConfig

logger = logging.getLogger(__name__)

# ── Vocal style rules — shared across scenarios ───────────────────────────────
_SPANISH_VOICE_RULES = """
=================================================
REGLAS GENERALES DE VOZ (OBLIGATORIAS)
=================================================
- Conversación telefónica natural y fluida.
- Respuestas cortas y directas. No des explicaciones largas.
- Responde siempre en español de España, con pronunciación peninsular.
- Evita seseo: pronuncia claramente "c" y "z" como español de España.
- Evita giros, entonación o dejes latinoamericanos.
- Voz adulta, estable y madura.
- Usa una entonación sobria, de locutor telefónico adulto.
- Evita cambios bruscos de tono dentro de una misma frase.
- No uses una prosodia juvenil, exagerada o inestable.
- Evita subidas repentinas de tono en palabras sueltas.
- No termines las frases con subida aguda de tono. Mantén una entonación descendente o neutra.
- Evita sonar cantarín. Usa una voz uniforme, estable y contenida.
- No uses una entonación excesivamente expresiva.
- No alargues vocales al final de las frases.
- No conviertas afirmaciones en preguntas por entonación.
- Cuando cierres una frase, baja ligeramente la entonación.
- Habla con claridad, sin sonar robótico.
"""

# ── Load prompt content from prompts/seleccion_1.md ───────────────────────────
CURRENT_DIR = Path(__file__).resolve().parent
PROMPT_FILE = CURRENT_DIR / "prompts" / "seleccion_1.md"

if not PROMPT_FILE.exists():
    raise FileNotFoundError(
        f"Prompt file for scenario 'seleccion_1' was not found at expected path: {PROMPT_FILE}"
    )

_SCENARIO_PROMPT = PROMPT_FILE.read_text(encoding="utf-8").strip()

# Composed instruction: general vocal style rules + specific roleplay prompt
_FULL_SYSTEM_INSTRUCTION = f"{_SPANISH_VOICE_RULES.strip()}\n\n{_SCENARIO_PROMPT}"

# ── Tool / Function Declarations ──────────────────────────────────────────────
SAVE_CANDIDATE_CONTEXT_TOOL = {
    "functionDeclarations": [
        {
            "name": "save_candidate_context",
            "description": "Guarda provisionalmente los datos identificativos y el consentimiento del candidato antes de iniciar la simulación.",
            "parameters": {
                "type": "object",
                "properties": {
                    "caller_user_name": {
                        "type": "string"
                    },
                    "caller_user_lastname": {
                        "type": "string"
                    },
                    "rgpd_ok": {
                        "type": "string",
                        "enum": ["Si"]
                    },
                    "scenario": {
                        "type": "string",
                        "enum": ["bm_atp_muro_doctor"]
                    }
                },
                "required": [
                    "caller_user_name",
                    "caller_user_lastname",
                    "rgpd_ok",
                    "scenario"
                ]
            }
        }
    ]
}


from app.core.gemini_session import OnboardingPhase

# ── Tool Handlers ─────────────────────────────────────────────────────────────
from app.scenarios.common import make_save_candidate_context_handler as _make_handler

def make_save_candidate_context_handler(session: Any) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Create a validated in-memory handler for save_candidate_context tool call."""
    return _make_handler(session, "bm_atp_muro_doctor")


def get_seleccion_1_handlers(session: Any) -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    """Retrieve all handlers mapped to this scenario."""
    return {
        "save_candidate_context": make_save_candidate_context_handler(session)
    }


# ── Scenario Configuration ────────────────────────────────────────────────────
SELECCION_1_CONFIG = ScenarioConfig(
    scenario_id="seleccion_1",
    display_name="Selección 1",
    description=(
        "Paciente que exige hablar inmediatamente con el doctor y se "
        "niega inicialmente a explicar el motivo de su consulta."
    ),
    system_instruction=_FULL_SYSTEM_INSTRUCTION,
    initial_message=(
        "Hola soy Miguel, asistente virtual para evaluación de candidatos "
        "de Boston Medical, esta es la primera prueba, ¿Estás preparado para comenzar?"
    ),
    external_scenario_id="bm_atp_muro_doctor",
    required_tools=["save_candidate_context"],
    tools=[SAVE_CANDIDATE_CONTEXT_TOOL],
    roleplay_transition_phrase="Perfecto, comenzamos la simulación. A partir de ahora soy el paciente.",
    roleplay_initial_phrase="Mira, quiero hablar con el doctor ahora mismo.",
    completion_phrase="La simulación ha terminado, gracias por participar en el proceso de selección de Boston Medical.",
    evaluation_agent_id="agent_3801kjcvpbseek68yv7hypkyhb7z",
)
