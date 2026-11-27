"""Confirm equipment from consecutive real observations, never transient previews."""
from datetime import datetime, timezone

from .models import Evidence


def missing_in_equipment_observation(state):
    """Only a completed inventory observation starts the live wait."""
    if not state.plan or state.mode != "live" or state.automation != "agent":
        return []
    step = state.plan["steps"][state.step]
    proof = state.facts.get("equipment_stability")
    observed = state.facts.get("observed_objects")
    if (step["gate"] != "equipment" or not proof or not observed
            or proof.value.get("step_id") != step["id"]
            or proof.reference != observed.reference or "objects" in state.facts):
        return []
    return [key for key in state.plan["required_objects"] if observed.value.get(key) is not True]


def record_equipment(engine, state, present, reference, captured_at, backend, automatic):
    source = "replay" if state.mode == "replay" else "vision"
    current = state.plan["steps"][state.step]
    old = state.facts.get("equipment_stability")
    previous = old.value if old and isinstance(old.value, dict) else {}
    frames = previous.get("frames", []) if previous.get("step_id") == current["id"] else []
    complete = all(present.get(key) is True for key in state.plan["required_objects"])
    instant = datetime.fromisoformat(captured_at)
    window = min(engine.settings.evidence_ttl_seconds, 90)
    frames = [frame for frame in frames
              if 0 <= (instant - datetime.fromisoformat(frame["at"])).total_seconds() <= window]
    if not complete:
        frames = []
        # A fresh negative result invalidates a previous automatic observation.
        objects = state.facts.get("objects")
        if objects and (objects.source == "vision" or
                        (objects.source == "replay" and old and objects.reference == old.reference)):
            state.facts.pop("objects")
    elif not frames or frames[-1]["reference"] != reference:
        frames.append({"at": captured_at, "reference": reference, "backend": backend})
        frames = frames[-2:]
    stable = (complete and len(frames) == 2
              and frames[0]["reference"] != frames[1]["reference"]
              and (datetime.fromisoformat(frames[1]["at"]) - datetime.fromisoformat(frames[0]["at"])).total_seconds() >= 1)
    progress = {"step_id": current["id"], "count": len(frames), "required": 2,
                "stable": stable, "frames": frames, "automatic": automatic}
    state.facts["equipment_stability"] = Evidence(value=progress, source=source, confidence=0,
                                                 reference=reference, at=captured_at)
    if stable and automatic:
        state.facts["objects"] = Evidence(value=state.plan["required_objects"], source=source, confidence=0,
                                          reference=reference, at=captured_at)
    return stable and automatic


def verified_visual_equipment(state, evidence):
    proof = state.facts.get("equipment_stability")
    if not proof or not isinstance(proof.value, dict):
        return False
    value = proof.value
    frames = value.get("frames", [])
    if not (value.get("stable") is True and value.get("automatic") is True and len(frames) == 2):
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(proof.at)).total_seconds()
        delta = (datetime.fromisoformat(frames[1]["at"]) - datetime.fromisoformat(frames[0]["at"])).total_seconds()
        return (age >= 0 and evidence.at == proof.at == frames[1]["at"]
                and frames[0]["reference"] != frames[1]["reference"]
                and evidence.reference == proof.reference == frames[1]["reference"] and delta >= 1)
    except (KeyError, TypeError, ValueError):
        return False
