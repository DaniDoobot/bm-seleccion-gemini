"""Base definition for voice scenario configurations.

A ScenarioConfig encapsulates everything a voice scenario needs to pass
to the Gemini Live session: the system instruction and, in future phases,
tools / function declarations specific to the scenario.

In this first phase, system_instruction is a minimal placeholder so that
the infrastructure can be validated end-to-end before the real prompts
are authored.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScenarioConfig:
    """Immutable configuration for a voice simulation scenario.

    Attributes:
        scenario_id:       Short slug identifier (e.g. "seleccion_1").
        display_name:      Human-readable name shown in the health check.
        system_instruction: Text sent to Gemini as the system instruction.
                           In Phase 1 this is a placeholder.
        tools:             Reserved for future function-calling declarations.
                           Empty list in Phase 1.
    """

    scenario_id: str
    display_name: str
    description: str
    system_instruction: str
    initial_message: str
    external_scenario_id: str
    required_tools: list[str] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)
    roleplay_transition_phrase: str = "Perfecto, comenzamos la simulación. A partir de ahora soy el paciente."
    roleplay_initial_phrase: str = ""
    completion_phrase: str = "La prueba ha terminado. Gracias por participar."
    evaluation_agent_id: str = ""
