"""
Orchestrator — central brain API for JARVIS.

Replaces scattered routing in web_server.py with a single entry point that
enriches every LLM query with structured DroneState, FlightPhase, and Anomaly
context before handing off to JARVIS.

Responsibilities:
  1. route_to_jarvis()  — build enriched drone context, call ask_jarvis
  2. proactive_tick()   — monitor for emergency phase / critical anomalies,
                          fire rate-limited background LLM advisories
  3. process_log()      — passthrough to ask_gemini_log_analysis

Copilot fast-path and SocketIO room-targeting remain in web_server.py because
they need a client_id that the Orchestrator does not hold.
"""

import logging
import threading
import time
from typing import Callable, Optional

orch_logger = logging.getLogger("orchestrator")

# Minimum seconds between proactive JARVIS advisory calls (emergency/anomaly)
_PROACTIVE_MIN_INTERVAL_S = 60.0


class Orchestrator:
    """
    Central brain API.  web_server.py creates one instance and routes all
    chat, voice, and proactive telemetry events through it.

    Parameters
    ----------
    validator       DroneValidator instance — owns drone_state, phase_detector,
                    safety_engine, anomaly_detector.
    jarvis_mod      The JARVIS module (has ask_jarvis, ask_gemini_log_analysis).
    emit_fn         Callable(event_name, data) → None — broadcasts a named
                    SocketIO event to all connected clients.
                    Used only for proactive advisories (not room-targeted).
    """

    def __init__(
        self,
        validator,
        jarvis_mod=None,
        emit_fn: Optional[Callable] = None,
    ):
        self._validator = validator
        self._jarvis    = jarvis_mod
        self._emit_fn   = emit_fn or (lambda event, data: None)

        # Proactive advisory state (protected by _lock)
        self._lock                  = threading.Lock()
        self._last_proactive_s: float = 0.0
        self._last_advisory_phase: str = ""
        self._seen_anomaly_ids: set = set()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def route_to_jarvis(self, query: str, provider: str = "gemini") -> dict:
        """
        Build enriched drone context and call ask_jarvis.

        This is the primary chat/voice entry point for web_server.py.
        Copilot fast-path is intentionally excluded here — it is handled
        upstream in web_server.py so room-targeted SocketIO emits work.

        Returns a JARVIS response dict (same shape as ask_jarvis).
        """
        if not self._jarvis:
            return {"error": "JARVIS module not available"}

        param_ctx     = None
        mavlink_ctx   = None
        drone_context = None

        if self._validator and getattr(self._validator, "hardware_validated", False):
            param_ctx     = self._validator.categorized_params
            mavlink_ctx   = self._validator.ai_mavlink_ctx
            drone_context = self._build_drone_context()

        try:
            result = self._jarvis.ask_jarvis(
                query,
                parameter_context=param_ctx,
                mavlink_ctx=mavlink_ctx,
                provider=provider,
                drone_context=drone_context,
            )
        except Exception as e:
            orch_logger.error(f"ask_jarvis raised: {e}")
            return {"error": str(e)}

        return result if result else {"error": "Empty response from JARVIS"}

    def process_log(
        self,
        query: str,
        summary: dict,
        data: Optional[dict] = None,
        provider: str = "gemini",
    ) -> dict:
        """Passthrough to JARVIS log analysis."""
        if not self._jarvis:
            return {"error": "JARVIS module not available"}
        try:
            return self._jarvis.ask_gemini_log_analysis(
                query, summary, data, provider=provider
            )
        except Exception as e:
            orch_logger.error(f"Log analysis error: {e}")
            return {"error": str(e)}

    def proactive_tick(self) -> None:
        """
        Called each 2 Hz telemetry cycle.  Monitors for high-priority
        phase transitions (→ EMERGENCY) and newly-detected critical anomalies,
        then fires a rate-limited background JARVIS advisory emitted as a
        proactive_advisory SocketIO event.

        The advisory is fire-and-forget (daemon thread) so the telemetry loop
        is never blocked by an LLM call.
        """
        if not self._validator or not getattr(self._validator, "hardware_validated", False):
            return
        if not self._jarvis:
            return

        with self._lock:
            phase_snap   = self._validator.phase_detector.snapshot()
            anomaly_snap = self._validator.anomaly_detector.snapshot()

            current_phase = phase_snap.get("phase", "")
            now           = time.time()

            # ── Emergency phase entry ────────────────────────────────────
            emergency_trigger = (
                current_phase == "EMERGENCY"
                and self._last_advisory_phase != "EMERGENCY"
                and now - self._last_proactive_s >= _PROACTIVE_MIN_INTERVAL_S
            )

            # ── New critical anomaly ─────────────────────────────────────
            active_criticals = {
                a["anomaly_id"]
                for a in anomaly_snap.get("active_anomalies", [])
                if a.get("severity") == "critical"
            }
            new_criticals   = active_criticals - self._seen_anomaly_ids
            anomaly_trigger = bool(new_criticals) and (
                now - self._last_proactive_s >= _PROACTIVE_MIN_INTERVAL_S
            )

            self._last_advisory_phase = current_phase

            if not (emergency_trigger or anomaly_trigger):
                # Update seen set even when no trigger so stale IDs don't re-fire
                self._seen_anomaly_ids = (
                    self._seen_anomaly_ids & active_criticals
                )  # prune resolved
                return

            # Commit fire timestamp / seen set before spawning thread
            self._last_proactive_s   = now
            self._seen_anomaly_ids  |= active_criticals

            if emergency_trigger:
                advisory_query = (
                    "EMERGENCY: The drone has just entered an emergency flight phase. "
                    "Analyze the current state and provide immediate, prioritized guidance "
                    "in plain language."
                )
            else:
                names = ", ".join(
                    a["title"]
                    for a in anomaly_snap.get("active_anomalies", [])
                    if a["anomaly_id"] in new_criticals
                )
                advisory_query = (
                    f"CRITICAL ANOMALY DETECTED: {names}. "
                    "Analyze the current drone state and provide immediate guidance "
                    "in plain language."
                )

        threading.Thread(
            target=self._proactive_advisory_worker,
            args=(advisory_query,),
            daemon=True,
            name="orch-proactive",
        ).start()

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _build_drone_context(self) -> str:
        """
        Assemble a compact structured context block from live DroneState,
        FlightPhase, and AnomalyDetector snapshots.

        Injected into JARVIS prompts so the LLM can reason about the drone's
        overall situation without having to parse raw MAVLink message dicts.
        Returns an empty string if validator is not available.
        """
        v = self._validator
        if not v:
            return ""

        parts = []

        # ── DroneState ───────────────────────────────────────────────────
        ds = v.drone_state.snapshot()
        bv = ds.get("battery_voltage", 0) or 0
        bp = ds.get("battery_pct")
        batt_str = (
            f"{bp}% / {bv:.1f}V" if bp is not None and bv else
            f"{bp}%"             if bp is not None else
            f"{bv:.1f}V"         if bv else "unknown"
        )
        parts.append(
            f"DroneState: armed={ds.get('armed')}, "
            f"mode={ds.get('flight_mode')}, "
            f"alt={ds.get('rel_altitude_m', 0):.1f}m AGL, "
            f"groundspeed={ds.get('groundspeed_ms', 0):.1f}m/s, "
            f"climb={ds.get('climb_rate_ms', 0):.1f}m/s, "
            f"battery={batt_str}, "
            f"gps_fix={ds.get('gps_fix')}, sats={ds.get('satellites')}, "
            f"ekf_ok={ds.get('ekf_ok')}, failsafe={ds.get('failsafe')}, "
            f"rc_rssi={ds.get('rc_rssi')}, "
            f"roll={ds.get('roll_deg', 0):.1f}°, pitch={ds.get('pitch_deg', 0):.1f}°"
        )

        # ── FlightPhase ──────────────────────────────────────────────────
        ph = v.phase_detector.snapshot()
        parts.append(
            f"FlightPhase: {ph.get('phase')} "
            f"(active for {ph.get('phase_duration_s', 0):.0f}s, "
            f"airborne={ph.get('is_airborne')}, "
            f"safe_to_command={ph.get('is_safe_to_command')})"
        )

        # ── Active anomalies ─────────────────────────────────────────────
        an = v.anomaly_detector.snapshot()
        active = an.get("active_anomalies", [])
        if active:
            anomaly_lines = "; ".join(
                f"{a['title']} [{a['severity']}]: {a['description']}"
                for a in active
            )
            parts.append(f"ActiveAnomalies: {anomaly_lines}")
        else:
            parts.append("ActiveAnomalies: none")

        return "\n".join(parts)

    def _proactive_advisory_worker(self, advisory_query: str) -> None:
        """Background thread: call JARVIS for an advisory and broadcast it."""
        try:
            drone_context = self._build_drone_context()
            param_ctx     = getattr(self._validator, "categorized_params", None)
            mavlink_ctx   = getattr(self._validator, "ai_mavlink_ctx", None)

            result = self._jarvis.ask_jarvis(
                advisory_query,
                parameter_context=param_ctx,
                mavlink_ctx=mavlink_ctx,
                provider="gemini",   # proactive advisories use the default provider
                drone_context=drone_context,
            )

            message = result.get("message", "") if result else ""
            if message:
                self._emit_fn("proactive_advisory", {
                    "severity": "critical",
                    "title":    "JARVIS Advisory",
                    "message":  message,
                    "intent":   result.get("intent", "diagnostic"),
                })
                orch_logger.info(
                    f"Proactive advisory emitted ({len(message)} chars): "
                    f"{message[:100]}..."
                )
        except Exception as e:
            orch_logger.error(f"Proactive advisory worker raised: {e}")
