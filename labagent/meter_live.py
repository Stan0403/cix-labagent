"""Latest-frame meter previews, followed by a fresh-image, revision-bound atomic commit."""

import hashlib
import io
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4

from fastapi import File, Form, UploadFile
from PIL import ImageChops, ImageStat

from .models import Action, DomainError, StrictModel, now
from .perception import load_image
from .step_checks import record_result, result_for, rule_for
from .workflow import step


def signatures(image, roi):
    if (
        not isinstance(roi, list)
        or len(roi) != 4
        or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in roi)
    ):
        raise DomainError("未定位到清晰仪表，不能采用核验结果", 422)
    x, y, w, h = roi
    if w <= 0 or h <= 0 or x + w > 1.00001 or y + h > 1.00001:
        raise DomainError("仪表区域无效", 422)
    meter = image.crop(
        (
            int(x * image.width),
            int(y * image.height),
            round((x + w) * image.width),
            round((y + h) * image.height),
        )
    )
    if min(meter.size) < 32:
        raise DomainError("仪表区域过小，请靠近镜头", 422)
    regions = [(0, 0, 1, 1), (0.06, 0.28, 0.94, 0.78), (0.02, 0.70, 0.98, 1)]
    return [
        meter.crop(
            (round(a * meter.width), round(b * meter.height), round(c * meter.width), round(d * meter.height))
        ).resize((256, 256))
        for a, b, c, d in regions
    ]


def same_scene(previous, current):
    for a, b in zip(previous, current):
        diff = ImageChops.difference(a, b)
        maximum = ImageChops.lighter(ImageChops.lighter(*diff.split()[:2]), diff.split()[2])
        histogram = maximum.histogram()
        changed = sum(histogram[37:]) / (256 * 256)
        if changed > 0.002 or max(ImageStat.Stat(diff).mean) > 4:
            return False
    return True


def image_file(image, directory):
    data = io.BytesIO()
    image.save(data, format="JPEG", quality=95)
    raw = data.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (digest + ".jpg")).write_bytes(raw)
    return "image:" + digest


@dataclass
class Preview:
    session_id: str
    revision: int
    step_id: str
    started: float
    captured_at: str
    check: dict
    roi: list
    fingerprints: list
    image: object


class MeterPreviews:
    def __init__(self):
        self.lock = threading.RLock()
        self.items = OrderedDict()
        self.busy = set()
        self.latest = OrderedDict()

    def record_preview(self, session_id, result):
        with self.lock:
            self.latest[session_id] = (time.monotonic(), deepcopy(result))
            self.latest.move_to_end(session_id)
            while len(self.latest) > 32:
                self.latest.popitem(last=False)

    def put(self, proof):
        with self.lock:
            token = uuid4().hex
            self.items[token] = proof
            while len(self.items) > 4:
                self.items.popitem(last=False)
            return token

    def forget(self, session_id):
        with self.lock:
            self.latest.pop(session_id, None)
            for token in [key for key, proof in self.items.items() if proof.session_id == session_id]:
                self.items.pop(token)


class MeterInvalidation(StrictModel):
    preview_id: str


def mount_meter_live(app, settings, store, engine, gateway):
    previews = MeterPreviews()
    app.state.meter_previews = previews

    @app.get("/api/sessions/{session_id}/meter-live/status")
    def status(session_id: str):
        state = store.get(session_id)
        with previews.lock:
            record = previews.latest.get(session_id)
            if not record or time.monotonic() - record[0] > 60:
                return {"available": False, "current": False, "last_preview": None}
            result = deepcopy(record[1])
        current = (
            state.status == "running"
            and state.revision == result["revision"]
            and step(state)["id"] == result["step_id"]
        )
        return {"available": True, "current": current, "last_preview": result}

    def running(sid):
        state = store.get(sid)
        if not settings.meter_live_enabled or state.status != "running" or rule_for(state) != "meter_setup":
            raise DomainError("请在运行中的仪表设置步骤开启持续检查", 409)
        return state

    def read(file):
        try:
            raw = file.file.read(8_000_001)
        finally:
            file.file.close()
        if len(raw) > 8_000_000:
            raise DomainError("图片上限8MB", 413)
        return load_image(raw)

    @app.post("/api/sessions/{session_id}/meter-live/preview")
    def preview(session_id: str, file: UploadFile = File(...)):
        image = read(file)
        before = running(session_id)
        with previews.lock:
            if session_id in previews.busy:
                raise DomainError("正在检查上一帧，不会积压画面", 429)
            previews.busy.add(session_id)
        started, captured_at = time.monotonic(), now()
        try:
            buffer = io.BytesIO()
            sent = image.copy()
            sent.thumbnail((1600, 1600))
            sent.save(buffer, format="JPEG", quality=95)
            response = gateway.request(
                settings.ocr_base_url,
                "",
                "check_meter_live",
                files={"file": ("meter.jpg", buffer.getvalue(), "image/jpeg")},
            )
            check = result_for("meter_setup", response, before.plan["measurement"])
            check["capture_mode"] = "single_frame_snapshot"
            if check["passed"]:
                check["message"] = "本次照片中的仪表设置检查通过。"
            if not check["passed"]:
                check["message"] = check["message"].replace(
                    "调整后工作流会自动复查。", "调整后请点击重新检测。"
                )
            detail_views = response.get("detail_views", [])
            if response.get("detail_mode") == "separate-dial-terminals-v1":
                check["detail_mode"] = response["detail_mode"]
                check["detail_regions"] = [
                    {"part": view["part"], "roi": view["roi"]} for view in detail_views
                ]
            if response.get("hint") and not check["passed"]:
                check["message"] = response["hint"]
            after = running(session_id)
            if after.revision != before.revision or step(after)["id"] != step(before)["id"]:
                raise DomainError("实验状态已变化，本帧结果已丢弃", 409)
            previews.record_preview(
                session_id,
                {
                    "revision": before.revision,
                    "step_id": step(before)["id"],
                    "captured_at": captured_at,
                    "seconds": round(time.monotonic() - started, 3),
                    "check": check,
                    "model_used": response.get("model_used"),
                    "diagnostic": response.get("diagnostic"),
                },
            )
            roi = response.get("roi")
            token = None
            if check["passed"]:
                fingerprints = signatures(image, roi)
                token = previews.put(
                    Preview(
                        session_id,
                        before.revision,
                        step(before)["id"],
                        started,
                        captured_at,
                        check,
                        roi,
                        fingerprints,
                        image,
                    )
                )
            return {
                "preview": True,
                "check": check,
                "preview_id": token,
                "roi": roi,
                "captured_at": captured_at,
                "seconds": round(time.monotonic() - started, 3),
                "step_id": step(before)["id"],
                "requires_fresh_frame": False,
                "acceptance_mode": "single_frame_snapshot",
                "detail_views": detail_views,
                "diagnostic": response.get("diagnostic"),
                "model_used": response.get("model_used"),
            }
        finally:
            with previews.lock:
                previews.busy.discard(session_id)

    @app.post("/api/sessions/{session_id}/meter-live/discard")
    def discard(session_id: str, body: MeterInvalidation):
        store.get(session_id)
        with previews.lock:
            proof = previews.items.get(body.preview_id)
            if proof and proof.session_id == session_id:
                previews.items.pop(body.preview_id)
        return {"discarded": True}

    @app.post("/api/sessions/{session_id}/meter-live/accept-snapshot")
    def accept_snapshot(session_id: str, body: MeterInvalidation):
        running(session_id)
        with previews.lock:
            proof = previews.items.get(body.preview_id)
            if not proof or proof.session_id != session_id or time.monotonic() - proof.started > 30:
                raise DomainError("本次照片检查已过期或不可用，请重新拍照", 409)
            previews.items.pop(body.preview_id)
        payload = {"rule": "meter_setup", "captured_at": proof.captured_at}

        def apply(state):
            if (
                state.status != "running"
                or step(state)["id"] != proof.step_id
                or rule_for(state) != "meter_setup"
                or time.monotonic() - proof.started > 30
                or proof.check.get("passed") is not True
            ):
                raise DomainError("实验状态已变化，未采用本次照片检查", 409)
            reference = image_file(proof.image, settings.data_dir / "images")
            result = {
                **proof.check,
                "method": "single_frame_snapshot",
                "captured_at": proof.captured_at,
                "snapshot_accepted_at": now(),
                "inference_reference": reference,
            }
            record_result(state, result, reference)
            advanced = engine.apply(state, Action(kind="advance", automatic=True))
            if not advanced["accepted"]:
                raise DomainError(advanced["message"], 409)
            payload.update(reference=reference, checks=result)
            return {
                "accepted": True,
                "passed": True,
                "advanced": True,
                "message": "已记录本次照片的检查结果，进入下一步。",
            }

        state, result = store.mutate(session_id, "workflow.visual_check", payload, apply, proof.revision)
        return {**result, "session": engine.view(state)}

    @app.post("/api/sessions/{session_id}/meter-live/commit")
    def commit(session_id: str, preview_id: str = Form(...), file: UploadFile = File(...)):
        current_image = read(file)
        received = time.monotonic()
        running(session_id)
        with previews.lock:
            proof = previews.items.get(preview_id)
            if not proof or proof.session_id != session_id or received - proof.started > 30:
                raise DomainError("检查已过期或画面已变化，请等待最新画面", 409)
            previews.items.pop(preview_id)
            if not same_scene(proof.fingerprints, signatures(current_image, proof.roi)):
                raise DomainError("画面已变化，旧检查不会用于推进实验", 409)
            # Only accepted, fresh evidence is stored. Previews are never facts.
            payload = {"rule": "meter_setup", "captured_at": proof.captured_at, "checks": proof.check}

            def apply(state):
                if (
                    state.status != "running"
                    or step(state)["id"] != proof.step_id
                    or rule_for(state) != "meter_setup"
                    or time.monotonic() - received > 2.5
                    or time.monotonic() - proof.started > 30
                ):
                    raise DomainError("实验或画面已变化，未采用旧检查", 409)
                inference_ref = image_file(proof.image, settings.data_dir / "images")
                reference = image_file(current_image, settings.data_dir / "images")
                result = {
                    **proof.check,
                    "inference_reference": inference_ref,
                    "scene_verified_at": now(),
                    "method": "live_scene_verified",
                }
                record_result(state, result, reference)
                advanced = engine.apply(state, Action(kind="advance", automatic=True))
                if not advanced["accepted"]:
                    raise DomainError(advanced["message"], 409)
                payload.update(reference=reference, checks=result)
                return {
                    "accepted": True,
                    "passed": True,
                    "advanced": True,
                    "message": "最新画面核验通过，已进入下一步。",
                }

            state, result = store.mutate(session_id, "workflow.visual_check", payload, apply, proof.revision)
            return {**result, "session": engine.view(state)}
