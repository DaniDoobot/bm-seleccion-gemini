"""Tests for the scenario registry.

Validates that:
  - All expected scenarios are registered.
  - Scenario IDs and display names are correct.
  - Valid slugs resolve to the correct ScenarioConfig.
  - Invalid slugs return None.
  - ScenarioConfig is immutable (frozen dataclass).
  - seleccion_1 loads its markdown prompt correctly independent of CWD.
  - seleccion_1 contains all essential keywords and has the exact initial message.
  - seleccion_2 remains a placeholder.
"""
import os
import sys
from pathlib import Path

import pytest

from app.scenarios.base import ScenarioConfig
from app.scenarios.registry import get_scenario, list_scenarios, scenario_ids
from app.scenarios.seleccion_1 import SELECCION_1_CONFIG
from app.scenarios.seleccion_2 import SELECCION_2_CONFIG


EXPECTED_SCENARIO_IDS = {"seleccion_1", "seleccion_2"}


class TestScenarioRegistry:

    def test_all_expected_scenarios_registered(self):
        ids = set(scenario_ids())
        assert EXPECTED_SCENARIO_IDS.issubset(ids), (
            f"Missing scenarios: {EXPECTED_SCENARIO_IDS - ids}"
        )

    def test_list_scenarios_returns_all(self):
        scenarios = list_scenarios()
        ids = {s.scenario_id for s in scenarios}
        assert EXPECTED_SCENARIO_IDS.issubset(ids)

    def test_list_scenarios_sorted(self):
        scenarios = list_scenarios()
        ids = [s.scenario_id for s in scenarios]
        assert ids == sorted(ids)

    def test_get_scenario_valid_seleccion_1(self):
        sc = get_scenario("seleccion_1")
        assert sc is not None
        assert sc.scenario_id == "seleccion_1"

    def test_get_scenario_valid_seleccion_2(self):
        sc = get_scenario("seleccion_2")
        assert sc is not None
        assert sc.scenario_id == "seleccion_2"

    def test_get_scenario_unknown_returns_none(self):
        assert get_scenario("does_not_exist") is None

    def test_get_scenario_empty_string_returns_none(self):
        assert get_scenario("") is None

    def test_scenario_ids_list(self):
        ids = scenario_ids()
        assert "seleccion_1" in ids
        assert "seleccion_2" in ids


class TestScenarioConfigs:

    def test_seleccion_1_display_name(self):
        assert SELECCION_1_CONFIG.display_name == "Selección 1"

    def test_seleccion_2_display_name(self):
        assert SELECCION_2_CONFIG.display_name == "Selección 2"

    def test_seleccion_2_has_placeholder_marker(self):
        """seleccion_2 has the real prompt content."""
        assert "RETRASO EN ENVÍO DE MEDICACIÓN" in SELECCION_2_CONFIG.system_instruction

    def test_seleccion_1_has_spanish_voice_rules(self):
        instr = SELECCION_1_CONFIG.system_instruction
        assert "español de España" in instr

    def test_seleccion_2_has_spanish_voice_rules(self):
        instr = SELECCION_2_CONFIG.system_instruction
        assert "español de España" in instr

    def test_system_instructions_not_empty(self):
        assert SELECCION_1_CONFIG.system_instruction.strip()
        assert SELECCION_2_CONFIG.system_instruction.strip()

    def test_seleccion_2_has_required_tools(self):
        assert SELECCION_2_CONFIG.required_tools == ["save_candidate_context"]
        assert len(SELECCION_2_CONFIG.tools) == 1

    def test_scenario_config_is_immutable(self):
        """ScenarioConfig is a frozen dataclass and must not be mutable."""
        with pytest.raises((TypeError, AttributeError)):
            SELECCION_1_CONFIG.scenario_id = "modified"  # type: ignore[misc]

    def test_scenario_contains_no_reference_project_content(self):
        """Ensure no business logic from bm-analysis-service leaked in."""
        forbidden_terms = [
            "Doobot", "Dubot", "TrainingCallSession",
            "hubspot", "verify_agent_code", "hangup_call",
            "simulation_prompt", "training_report",
        ]
        for sc in [SELECCION_1_CONFIG, SELECCION_2_CONFIG]:
            for term in forbidden_terms:
                assert term.lower() not in sc.system_instruction.lower(), (
                    f"Forbidden term '{term}' found in {sc.scenario_id} system instruction."
                )

    # ── seleccion_1 real prompt tests ─────────────────────────────────────────

    def test_seleccion_1_loads_correct_markdown(self):
        """Verify that the markdown file exists and matches the system instruction."""
        prompt_path = Path(__file__).parent.parent / "app" / "scenarios" / "prompts" / "seleccion_1.md"
        assert prompt_path.exists()
        prompt_content = prompt_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert prompt_content.strip()
        system_instr = SELECCION_1_CONFIG.system_instruction.replace("\r\n", "\n")
        assert prompt_content.strip() in system_instr

    def test_seleccion_1_prompt_not_empty(self):
        assert SELECCION_1_CONFIG.system_instruction.strip()

    def test_seleccion_1_essential_markers(self):
        instr = SELECCION_1_CONFIG.system_instruction
        assert "MIGUEL PÉREZ GÓMEZ" in instr
        assert "save_candidate_context" in instr
        assert "bm_atp_muro_doctor" in instr
        assert "La prueba ha terminado. Gracias por participar." in instr

    def test_seleccion_1_initial_message(self):
        expected = (
            "Hola soy Miguel, asistente virtual para evaluación de candidatos "
            "de Boston Medical, esta es la primera prueba, ¿Estás preparado para comenzar?"
        )
        assert SELECCION_1_CONFIG.initial_message == expected

    def test_seleccion_1_external_id(self):
        assert SELECCION_1_CONFIG.external_scenario_id == "bm_atp_muro_doctor"

    def test_seleccion_1_required_tools(self):
        assert SELECCION_1_CONFIG.required_tools == ["save_candidate_context"]

    def test_seleccion_1_loads_independent_of_cwd(self):
        """Simulate loading app.scenarios.seleccion_1 from a different CWD."""
        import importlib
        import app.scenarios.seleccion_1

        original_cwd = os.getcwd()
        try:
            # Change CWD to project root parent
            os.chdir(str(Path(__file__).parent.parent.parent))
            # Reload module
            importlib.reload(app.scenarios.seleccion_1)
            # Verify it loaded without error and contains the prompt
            assert app.scenarios.seleccion_1.SELECCION_1_CONFIG.system_instruction
            assert "MIGUEL PÉREZ GÓMEZ" in app.scenarios.seleccion_1.SELECCION_1_CONFIG.system_instruction
        finally:
            os.chdir(original_cwd)


class TestSeleccion2Config:
    """Minimal unit tests verifying seleccion_2 scenario integration and transitions."""

    def test_seleccion_2_is_registered(self):
        from app.scenarios.registry import get_scenario, scenario_ids
        assert "seleccion_2" in scenario_ids()
        config = get_scenario("seleccion_2")
        assert config is not None
        assert config.display_name == "Selección 2"

    def test_seleccion_2_prompt_and_id(self):
        from app.scenarios.seleccion_2 import SELECCION_2_CONFIG
        assert SELECCION_2_CONFIG.external_scenario_id == "bm_atp_retraso_envio_baja"
        assert "RETRASO EN ENVÍO DE MEDICACIÓN" in SELECCION_2_CONFIG.system_instruction
        assert "segunda prueba" in SELECCION_2_CONFIG.initial_message

    def test_seleccion_2_tool_scenario_enum(self):
        from app.scenarios.seleccion_2 import SELECCION_2_CONFIG
        tool_decl = SELECCION_2_CONFIG.tools[0]
        properties = tool_decl["functionDeclarations"][0]["parameters"]["properties"]
        assert properties["scenario"]["enum"] == ["bm_atp_retraso_envio_baja"]

    def test_seleccion_2_transitions_to_active(self):
        from app.core.gemini_session import GeminiVoiceSession, OnboardingPhase
        from app.config import get_settings
        from app.scenarios.seleccion_2 import SELECCION_2_CONFIG

        session = GeminiVoiceSession(
            settings=get_settings(),
            system_instruction=SELECCION_2_CONFIG.system_instruction,
            roleplay_transition_phrase=SELECCION_2_CONFIG.roleplay_transition_phrase,
            roleplay_initial_phrase=SELECCION_2_CONFIG.roleplay_initial_phrase,
            completion_phrase=SELECCION_2_CONFIG.completion_phrase,
        )

        session.onboarding_phase = OnboardingPhase.READY_TO_START_ROLEPLAY
        # Simulated transition phrase matching seleccion_2
        session._model_transcript_accumulator = (
            "Perfecto, comenzamos la simulación. A partir de ahora soy el paciente. "
            "Mira, llamo porque esto ya me parece inadmisible. Ya reclamé un problema con el envío y sigo igual. Así no puedo seguir."
        )

        norm_accumulated = session._normalize_text(session._model_transcript_accumulator)
        norm_p1 = session._normalize_text(session._roleplay_transition_phrase)
        norm_p2 = session._normalize_text(session._roleplay_initial_phrase)
        p1_idx = norm_accumulated.find(norm_p1)
        p2_idx = norm_accumulated.find(norm_p2)

        if p1_idx != -1 and p2_idx != -1 and p1_idx < p2_idx:
            session.onboarding_phase = OnboardingPhase.ROLEPLAY_ACTIVE

        assert session.onboarding_phase == OnboardingPhase.ROLEPLAY_ACTIVE

    def test_seleccion_2_transitions_to_finished(self):
        from app.core.gemini_session import GeminiVoiceSession, OnboardingPhase
        from app.config import get_settings
        from app.scenarios.seleccion_2 import SELECCION_2_CONFIG

        session = GeminiVoiceSession(
            settings=get_settings(),
            system_instruction=SELECCION_2_CONFIG.system_instruction,
            roleplay_transition_phrase=SELECCION_2_CONFIG.roleplay_transition_phrase,
            roleplay_initial_phrase=SELECCION_2_CONFIG.roleplay_initial_phrase,
            completion_phrase=SELECCION_2_CONFIG.completion_phrase,
        )

        session.onboarding_phase = OnboardingPhase.ROLEPLAY_ACTIVE
        session._model_transcript_accumulator = "La prueba ha terminado. Gracias por participar."

        norm_accumulated = session._normalize_text(session._model_transcript_accumulator)
        norm_closure = session._normalize_text(session._completion_phrase)
        if norm_closure in norm_accumulated:
            session.onboarding_phase = OnboardingPhase.ROLEPLAY_FINISHED

        assert session.onboarding_phase == OnboardingPhase.ROLEPLAY_FINISHED
