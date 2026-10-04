"""
Pause and Private Mode — the switches, and where they are enforced.

ENFORCEMENT IS AT THE EVENT HANDLER, NOT AT THE UI AND NOT AT THE DATABASE.

A privacy switch that is honoured when the record is written has already lost:
the path has been read, passed through a classifier, held in a list, and
possibly logged by something on the way. This gate is consulted in the
filesystem handler before an event object is constructed at all, so when it is
closed nothing downstream ever learns that a file changed.

THE TWO SWITCHES ARE NOT THE SAME THING, and the difference is what people
actually need:

  PAUSE          "stop for now" — reversible, remembered across restarts, and
                 the normal control. Used when stepping into a meeting or
                 working on something personal for an hour.

  PRIVATE MODE   "stop, and do not remember that I stopped in a way that
                 reconstructs the gap" — also reversible, but it resets on
                 restart rather than persisting. It is the stronger statement
                 and the one to reach for by reflex, so it is deliberately not
                 sticky: a mode that silently stays on for three weeks is a
                 mode that quietly stops the product working.

Both are audited as EVENTS — "private mode on at 14:02", "off at 15:30". The
audit records that collection stopped; it does not record what happened during
the gap, because there is nothing to record. Auditing the switch is what makes
the switch trustworthy: a control nobody can verify was used is a checkbox.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger(__name__)


class TrackingState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    PRIVATE = "private"


@dataclass(frozen=True)
class PrivacyStatus:
    """What the UI shows. Flat and boring on purpose — it is read constantly."""

    state: TrackingState
    collecting: bool
    reason: str

    @property
    def label(self) -> str:
        return {
            TrackingState.ACTIVE: "Tracking Active",
            TrackingState.PAUSED: "Tracking Paused",
            TrackingState.PRIVATE: "Private Mode",
        }[self.state]


class PrivacyGate:
    """
    The single place that answers "may I collect this?".

    Every tracker holds one of these and calls `allows_collection()` before it
    does anything at all with an event. There is deliberately no second way to
    ask — no module reads `config.privacy.paused` directly, because two readers
    of the same flag eventually disagree about what it means.

    Thread-safe: the filesystem watcher runs on watchdog's observer thread
    while the UI flips switches on the main one, and a torn read here is a
    recorded event during private mode.
    """

    def __init__(
        self,
        *,
        paused: bool = False,
        private_mode: bool = False,
        audit_sink=None,
    ) -> None:
        self._lock = threading.RLock()
        self._paused = bool(paused)
        # NOT restored from config: private mode resets on every start. See the
        # module docstring — stickiness is the failure mode here, not a feature.
        self._private = bool(private_mode)
        self._audit = audit_sink

    # ------------------------------------------------------------------
    def allows_collection(self) -> bool:
        """The hot path. Called once per filesystem event, so it stays cheap."""
        with self._lock:
            return not (self._paused or self._private)

    def status(self) -> PrivacyStatus:
        with self._lock:
            if self._private:
                return PrivacyStatus(
                    TrackingState.PRIVATE, False,
                    "Private Mode is on. Nothing is being recorded.",
                )
            if self._paused:
                return PrivacyStatus(
                    TrackingState.PAUSED, False,
                    "Tracking is paused. Nothing is being recorded.",
                )
            return PrivacyStatus(
                TrackingState.ACTIVE, True,
                "Tracking approved folders. Raw activity stays on this device.",
            )

    # ------------------------------------------------------------------
    def pause(self) -> PrivacyStatus:
        with self._lock:
            changed = not self._paused
            self._paused = True
        if changed:
            self._record("tracking_paused")
        return self.status()

    def resume(self) -> PrivacyStatus:
        """
        Resume clears BOTH switches.

        Someone who hits Resume means "start working normally again". Leaving
        private mode on because it was set separately produces a program that
        says Active in one place and records nothing — the exact confusion
        these controls exist to prevent.
        """
        with self._lock:
            changed = self._paused or self._private
            was_private = self._private
            self._paused = False
            self._private = False
        if was_private:
            self._record("private_mode_disabled")
        if changed:
            self._record("tracking_resumed")
        return self.status()

    def enable_private_mode(self) -> PrivacyStatus:
        with self._lock:
            changed = not self._private
            self._private = True
        if changed:
            self._record("private_mode_enabled")
        return self.status()

    def disable_private_mode(self) -> PrivacyStatus:
        with self._lock:
            changed = self._private
            self._private = False
        if changed:
            self._record("private_mode_disabled")
        return self.status()

    # ------------------------------------------------------------------
    def _record(self, action: str) -> None:
        """
        Audit the switch, never the gap.

        No detail argument is accepted on purpose. An audit line reading
        "private mode enabled while editing salary-review.xlsx" would leak the
        thing private mode was turned on to protect.
        """
        log.info("privacy: %s", action)
        if self._audit is None:
            return
        try:
            self._audit.record(action)
        except Exception:  # pragma: no cover - auditing must never break tracking
            log.exception("Could not write audit entry for %s", action)
