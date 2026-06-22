"""Patient & operations metric emitter for the base healthcare dashboard.

Pumps realistic values for `hospital.patient.*`, `hospital.staffing.*`,
`hospital.physician.*`, and `hospital.telemetry.*` directly to the Datadog
Agent over DogStatsD (UDP :8125). Designed to fill the
"Patient Experience & Clinical Operations" dashboard for the AdventHealth
demo when the base simulator's plugins don't actively drive these metrics.

Floor 3 East intentionally emits *worse* values than other floors so the
dashboard's "Floor 3 East — Correlation Analysis" panel tells the right
story (lowest satisfaction, longest wait times, highest alarm gaps).

Tag keys match the dashboard's template variables: floor, wing, shift.
"""
from __future__ import annotations

import os
import random
import socket
import time

AGENT_HOST = os.getenv("DD_AGENT_HOST", "datadog-agent-sh")
AGENT_PORT = int(os.getenv("DD_DOGSTATSD_PORT", "8125"))
INTERVAL_SEC = int(os.getenv("PATIENT_OPS_INTERVAL_SEC", "15"))

FLOORS = ["1", "2", "3", "4", "5"]
WINGS = ["east", "west", "north", "south"]
SHIFTS = ["day", "night", "weekend"]

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# These metrics are registered in Datadog's metric catalog as DISTRIBUTION
# type (originally emitted as OTel histograms by the base healthcare
# simulator's plugins). DogStatsD type-mismatched submissions are *silently
# dropped* — sending a `|g` (gauge) for a metric registered as distribution
# means the datapoint never appears. Emit these as `|d` instead.
DISTRIBUTION_METRICS = {
    "hospital.physician.order_to_admin_min",
    "hospital.physician.patient_interaction_min",
    "hospital.patient.wait_time_min",
    "hospital.patient.pain_response_time_min",
    "hospital.patient.discharge_process_hours",
    "hospital.telemetry.alarm_response_time_sec",
}


def emit(name: str, value: float, tags: list[str]) -> None:
    """Emit a single DogStatsD metric over UDP, picking gauge or distribution
    type based on what Datadog has the metric registered as. Errors are
    swallowed; this is a best-effort publisher and the agent's not always
    immediately reachable at container start."""
    try:
        metric_type = "d" if name in DISTRIBUTION_METRICS else "g"
        msg = f"{name}:{value}|{metric_type}|#{','.join(tags)}".encode()
        sock.sendto(msg, (AGENT_HOST, AGENT_PORT))
    except Exception:
        pass


# Backward-compat: existing callers used emit_gauge; route through emit().
emit_gauge = emit


def jitter(low: float, high: float, ndigits: int = 2) -> float:
    """Uniform-random value in [low, high] rounded to `ndigits` decimals."""
    return round(random.uniform(low, high), ndigits)


def values_for(floor: str, wing: str, shift: str) -> dict[str, float]:
    """Pick a metric set for this cell. Floor-3 East gets the 'problem floor'
    profile (worst satisfaction, longest waits, more alarm noise). The night
    + weekend shifts also degrade slightly relative to day, matching how
    real hospitals report HCAHPS swings."""
    f3e = (floor == "3" and wing == "east")
    night = shift in ("night", "weekend")

    # Multipliers for the two condition axes — combined for additive degrade.
    bump = 1.0 + (0.55 if f3e else 0.0) + (0.18 if night else 0.0)

    return {
        # HCAHPS + patient satisfaction (5-point scales). Lower = worse.
        "hospital.patient.hcahps_score": jitter(4.4 - 0.9 * (bump - 1), 4.7 - 0.6 * (bump - 1)),
        "hospital.patient.satisfaction_nurse_communication": jitter(4.3 - 0.8 * (bump - 1), 4.6 - 0.5 * (bump - 1)),
        "hospital.patient.satisfaction_pain_management": jitter(4.2 - 0.8 * (bump - 1), 4.5 - 0.5 * (bump - 1)),
        "hospital.patient.satisfaction_responsiveness": jitter(4.1 - 0.9 * (bump - 1), 4.4 - 0.6 * (bump - 1)),
        "hospital.patient.satisfaction_discharge_info": jitter(4.3 - 0.7 * (bump - 1), 4.6 - 0.4 * (bump - 1)),

        # Patient flow / response times (lower is better).
        "hospital.patient.wait_time_min": jitter(22 * bump, 38 * bump, 1),
        "hospital.patient.pain_response_time_min": jitter(8 * bump, 18 * bump, 1),
        "hospital.patient.discharge_process_hours": jitter(2.0 * bump, 3.8 * bump, 2),

        # Physician engagement (mix of higher/lower-is-better).
        "hospital.physician.rounds_completed": jitter(3, 9, 0),
        "hospital.physician.mobile_orders_placed": jitter(12, 38, 0),
        "hospital.physician.mobile_results_viewed": jitter(20, 55, 0),
        "hospital.physician.order_to_admin_min": jitter(18 * bump, 42 * bump, 1),
        "hospital.physician.patient_interaction_min": jitter(11, 22, 1),

        # Staffing (operational context for the correlation panels).
        "hospital.staffing.nurses_on_duty": jitter(14 / bump, 28 / bump, 0),
        "hospital.staffing.nurse_to_patient_ratio": jitter(4 * bump, 7 * bump, 1),
        "hospital.staffing.open_positions": jitter(3 * bump, 12 * bump, 0),
        "hospital.staffing.overtime_hours": jitter(20 * bump, 60 * bump, 0),
        "hospital.staffing.turnover_rate_pct": jitter(9 * bump, 16 * bump, 1),

        # Clinical-telemetry monitoring (lower is generally better).
        "hospital.telemetry.active_alarms": jitter(55 * bump, 220 * bump, 0),
        "hospital.telemetry.alarm_response_time_sec": jitter(35 * bump, 110 * bump, 0),
        "hospital.telemetry.false_alarm_rate_pct": jitter(15 * bump, 35 * bump, 1),
        "hospital.telemetry.monitoring_gap_events": jitter(0, 8 * bump, 0),
    }


def tick() -> int:
    """Emit one round across every (floor, wing, shift) cell. Returns count."""
    n = 0
    for floor in FLOORS:
        for wing in WINGS:
            for shift in SHIFTS:
                tags = [f"floor:{floor}", f"wing:{wing}", f"shift:{shift}",
                        "dd-demo-toolkit:true", "vertical:healthcare",
                        "sub_vertical:adventhealth"]
                for name, value in values_for(floor, wing, shift).items():
                    emit_gauge(name, value, tags)
                    n += 1
    return n


def main() -> None:
    print(f"[patient-ops] emitting → {AGENT_HOST}:{AGENT_PORT} every {INTERVAL_SEC}s", flush=True)
    while True:
        count = tick()
        print(f"[patient-ops] tick: {count} metrics emitted", flush=True)
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
