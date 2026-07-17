"""Shared helper functions for voice scenarios.

Avoids duplicate candidate context saving logic across scenarios.
"""
import logging
from typing import Any, Callable, Dict
from app.core.gemini_session import OnboardingPhase

logger = logging.getLogger(__name__)


def make_save_candidate_context_handler(
    session: Any,
    expected_scenario: str,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Create a validated in-memory handler for save_candidate_context tool call."""
    def handler(args: Dict[str, Any]) -> Dict[str, Any]:
        # 1. Handle duplicate calls first (only execute once per session)
        if session.candidate_context.get("saved"):
            logger.info("save_candidate_context: Duplicate execution detected and ignored.")
            return {
                "success": False,
                "error": "already_saved",
                "message": "Candidate context was already saved for this session.",
                "caller_user_name": session.candidate_context.get("caller_user_name"),
                "caller_user_lastname": session.candidate_context.get("caller_user_lastname"),
            }

        # 2. Enforce strict phase state machine control
        current_phase_obj = getattr(session, "onboarding_phase", None)
        if current_phase_obj != OnboardingPhase.READY_TO_SAVE:
            logger.warning(
                "Premature save_candidate_context call detected. Current Phase: %s",
                current_phase_obj
            )
            current_phase_str = current_phase_obj.value if hasattr(current_phase_obj, "value") else str(current_phase_obj)
            return {
                "success": False,
                "error": "onboarding_incomplete",
                "required_phase": "ready_to_save",
                "current_phase": current_phase_str,
                "message": "Los datos y el consentimiento RGPD todavía no se han confirmado en el orden requerido."
            }

        name = args.get("caller_user_name")
        lastname = args.get("caller_user_lastname")
        rgpd = args.get("rgpd_ok")
        scenario = args.get("scenario")

        # 3. Validation of existence
        if name is None or lastname is None or rgpd is None or scenario is None:
            raise ValueError("All fields are required: name, lastname, rgpd_ok, scenario.")

        # 4. String sanitation (stripping spaces)
        name_str = str(name).strip()
        lastname_str = str(lastname).strip()

        # 5. Validation of empty values
        if not name_str:
            raise ValueError("caller_user_name cannot be empty.")
        if not lastname_str:
            raise ValueError("caller_user_lastname cannot be empty.")

        # 6. Domain value validation
        rgpd_str = str(rgpd).strip().lower() if rgpd is not None and not isinstance(rgpd, bool) else ""
        if rgpd is True or rgpd_str in ["si", "sí", "true"]:
            rgpd_bool = True
        else:
            raise ValueError("rgpd_ok must represent a valid acceptance ('Si', 'Sí' or 'True').")

        if scenario != expected_scenario:
            raise ValueError(f"scenario must be '{expected_scenario}'.")

        # 6. Save in session context state
        session.candidate_context.update({
            "caller_user_name": name_str,
            "caller_user_lastname": lastname_str,
            "rgpd_ok": rgpd_bool,
            "scenario": scenario,
            "saved": True
        })
        session.onboarding_phase = OnboardingPhase.CONTEXT_SAVED

        # Start call recording if call_sid is present, enabled, and not yet started
        call_sid = getattr(session, "call_sid", None)
        if call_sid and not getattr(session, "recording_start_attempted", False):
            session.recording_start_attempted = True
            import asyncio
            from app.services.twilio_recording import start_twilio_recording

            async def _bg_record():
                try:
                    logger.info("Triggering background Twilio recording. CallSid=%s", call_sid)
                    scenario_id = getattr(session, "scenario_id", "seleccion_1")
                    rec_sid = start_twilio_recording(call_sid, scenario_id)
                    if rec_sid:
                        session.recording_sid = rec_sid
                        session.recording_started = True
                        session.recording_status = "in_progress"
                        logger.info("Recording started in background task. RecordingSid=%s", rec_sid)
                    else:
                        session.recording_error = "failed_to_start"
                        logger.warning("Recording start in background failed for CallSid=%s", call_sid)
                except Exception as e:
                    logger.error("Error starting recording in background task: %s", e)

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_bg_record())
            except RuntimeError:
                # No running event loop (e.g. during unit tests)
                asyncio.run(_bg_record())


        # 7. Safe logging (masking personal names to protect privacy)
        masked_name = name_str[0] + "*" * (len(name_str) - 1) if name_str else ""
        masked_lastname = lastname_str[0] + "*" * (len(lastname_str) - 1) if lastname_str else ""
        logger.info(
            "save_candidate_context: candidate saved successfully. "
            "Name: %s %s, RGPD: %s, Scenario: %s",
            masked_name, masked_lastname, rgpd, scenario
        )

        return {
            "success": True,
            "message": "Candidate context saved successfully."
        }

    return handler
