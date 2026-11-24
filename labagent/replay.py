from pydantic import ValidationError

from .models import Action, DomainError, Evidence, NewSession


def replay(store, engine, request):
    # Validate every event before creating a session; mode is immutable in storage/API.
    prepared = []
    try:
        for event in request.events:
            data = event.data
            if event.kind == "action":
                action = Action.model_validate(data)
                action.confirmed = True
                prepared.append((event, action))
            elif event.kind == "objects":
                if set(data) != {"objects"} or not isinstance(data["objects"], list):
                    raise ValueError()
                if not all(isinstance(x, str) and x in engine.spec["required_objects"] for x in data["objects"]):
                    raise ValueError()
                prepared.append((event, Action(kind="confirm_equipment", objects=data["objects"], confirmed=True)))
            elif event.kind == "measurement":
                if set(data) != {"value"}:
                    raise ValueError()
                prepared.append((event, Action(kind="manual_measurement", value=data["value"], confirmed=True)))
            else:
                if event.kind == "power":
                    if set(data) != {"value"} or data["value"] not in {"off", "on"}:
                        raise ValueError()
                    kind = "confirm_power_" + data["value"]
                else:
                    if data != {"value": True}:
                        raise ValueError()
                    kind = "confirm_connected" if event.kind == "connected" else "confirm_wiring"
                prepared.append((event, Action(kind=kind, confirmed=True)))
    except (ValueError, TypeError, ValidationError) as exc:
        raise DomainError("回放事件格式错误，请参考 fixtures 中的格式", 422) from exc
    state = store.create(NewSession(title="回放 · " + request.name, mode="replay"))
    results = []
    for event, action in prepared:
        state, result = store.mutate(state.id, "replay." + event.kind, event.data,
                                     lambda current, a=action: engine.apply(current, a, source="replay"))
        results.append({"event": event.kind, **result, "step": state.step})
    return {"session": engine.view(state), "results": results}


def apply_detection(state, detections, engine, reference):
    if state.status != "running":
        return {"accepted": False, "message": "实验不在运行中，未更新器材"}
    if state.step < 2:
        return {"accepted": False, "message": "请先完成阅读说明和断电确认，再检查器材"}
    accepted = [d for d in detections if d["confidence"] >= engine.settings.min_confidence]
    objects = sorted({d["label"] for d in accepted})
    state.facts["objects"] = Evidence(value=objects, source="replay" if state.mode == "replay" else "vision",
                                      reference=reference, confidence=min((d["confidence"] for d in accepted), default=1))
    return {"accepted": True, "message": "已更新可见器材；这不能证明电源或接线安全。", "objects": objects}
