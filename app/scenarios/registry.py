"""Scenario registry.

All available scenarios are registered here. The voice router resolves a
scenario by its slug (e.g. "seleccion_1") and retrieves the corresponding
ScenarioConfig without duplicating Gemini engine logic.
"""
from typing import Dict, Optional

from app.scenarios.base import ScenarioConfig
from app.scenarios.seleccion_1 import SELECCION_1_CONFIG
from app.scenarios.seleccion_2 import SELECCION_2_CONFIG

# Master registry: scenario_id → ScenarioConfig
_REGISTRY: Dict[str, ScenarioConfig] = {
    SELECCION_1_CONFIG.scenario_id: SELECCION_1_CONFIG,
    SELECCION_2_CONFIG.scenario_id: SELECCION_2_CONFIG,
}


def get_scenario(scenario_id: str) -> Optional[ScenarioConfig]:
    """Return the ScenarioConfig for the given slug, or None if not found."""
    return _REGISTRY.get(scenario_id)


def list_scenarios() -> list[ScenarioConfig]:
    """Return all registered scenarios ordered by scenario_id."""
    return sorted(_REGISTRY.values(), key=lambda s: s.scenario_id)


def scenario_ids() -> list[str]:
    """Return all registered scenario slugs."""
    return sorted(_REGISTRY.keys())


def get_scenario_handlers(scenario_id: str, session: Any) -> Dict[str, Any]:
    """Retrieve the tool handlers dictionary for a given scenario."""
    if scenario_id == "seleccion_1":
        from app.scenarios.seleccion_1 import get_seleccion_1_handlers
        return get_seleccion_1_handlers(session)
    elif scenario_id == "seleccion_2":
        from app.scenarios.seleccion_2 import get_seleccion_2_handlers
        return get_seleccion_2_handlers(session)
    return {}
