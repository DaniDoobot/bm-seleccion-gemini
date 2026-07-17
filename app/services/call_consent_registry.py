import datetime
import logging
from typing import Dict, Optional, Literal

logger = logging.getLogger(__name__)

class CallConsentEntry:
    def __init__(self, call_sid: str, scenario_id: str):
        self.call_sid: str = call_sid
        self.scenario_id: str = scenario_id
        self.consent_status: Literal["pending", "accepted", "rejected"] = "pending"
        self.recording_sid: Optional[str] = None
        self.recording_status: Optional[str] = None
        self.pending_recording_event: Optional[dict] = None
        self.created_at: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)

class CallConsentRegistry:
    def __init__(self, ttl_seconds: int = 86400):
        self._entries: Dict[str, CallConsentEntry] = {}
        self._ttl_seconds = ttl_seconds
        import threading
        self._lock = threading.Lock()

    def get_or_create(self, call_sid: str, scenario_id: str) -> CallConsentEntry:
        with self._lock:
            self._cleanup_expired()
            if call_sid not in self._entries:
                self._entries[call_sid] = CallConsentEntry(call_sid, scenario_id)
                logger.info("[Registry] Created entry for CallSid=%s, Scenario=%s", call_sid, scenario_id)
            return self._entries[call_sid]

    def get(self, call_sid: str) -> Optional[CallConsentEntry]:
        with self._lock:
            self._cleanup_expired()
            return self._entries.get(call_sid)

    def update_consent(self, call_sid: str, consent_status: Literal["pending", "accepted", "rejected"]) -> Optional[CallConsentEntry]:
        with self._lock:
            self._cleanup_expired()
            if call_sid in self._entries:
                entry = self._entries[call_sid]
                entry.consent_status = consent_status
                logger.info("[Registry] Updated consent for CallSid=%s to %s", call_sid, consent_status)
                return entry
            return None

    def update_recording(self, call_sid: str, recording_sid: str, recording_status: str) -> Optional[CallConsentEntry]:
        with self._lock:
            self._cleanup_expired()
            if call_sid in self._entries:
                entry = self._entries[call_sid]
                entry.recording_sid = recording_sid
                entry.recording_status = recording_status
                logger.info("[Registry] Updated recording for CallSid=%s to Sid=%s, Status=%s", call_sid, recording_sid, recording_status)
                return entry
            return None

    def set_pending_event(self, call_sid: str, event: dict) -> None:
        with self._lock:
            self._cleanup_expired()
            if call_sid in self._entries:
                self._entries[call_sid].pending_recording_event = event
                logger.info("[Registry] Saved pending recording event for CallSid=%s", call_sid)

    def remove(self, call_sid: str) -> None:
        with self._lock:
            if call_sid in self._entries:
                del self._entries[call_sid]
                logger.info("[Registry] Removed entry for CallSid=%s", call_sid)

    def _cleanup_expired(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        expired_keys = [
            k for k, v in self._entries.items()
            if (now - v.created_at).total_seconds() > self._ttl_seconds
        ]
        for k in expired_keys:
            del self._entries[k]
            logger.info("[Registry] Cleaned up expired entry CallSid=%s", k)

# Global registry instance
registry = CallConsentRegistry()
