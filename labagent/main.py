import hashlib
import json
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .agent import Agent
from .config import ROOT, Settings
from .knowledge import Knowledge
from .models import Action, ChatRequest, DomainError, NewSession, ReplayRequest, StrictModel
from .perception import Perception, apply_meter_range, load_image
from .providers import Gateway
from .replay import apply_detection, replay
from .report import html_report, markdown_report, measurements_csv
from .rules import Engine
from .store import Store
from .training_photos import MAX_BYTES as MAX_PHOTO_BYTES
from .training_photos import photo_count, save_photo


class SerialReading(StrictModel):
    value: float
    unit: Literal["V"] = "V"
    device: str = Field(min_length=1, max_length=100)
    expected_revision: int | None = None


class SessionReview(StrictModel):
    previous_session_id: str | None = Field(default=None, max_length=80)


class SessionDeletion(StrictModel):
    confirmed: bool = False
    expected_revision: int = Field(ge=0)


def create_app(settings: Settings | None = None, gateway=None):
    settings = settings or Settings.load()
    if settings.host not in {"localhost", "127.0.0.1", "::1"} and not settings.access_token:
        raise ValueError("非本机监听必须配置 LABAGENT_ACCESS_TOKEN；如需远程访问还应部署 HTTPS")
    data = settings.data_dir
    data.mkdir(parents=True, exist_ok=True)
    knowledge_path = data / "knowledge"
    knowledge_path.mkdir(exist_ok=True)
    for path in (ROOT / "knowledge").glob("*.md"):
        destination = knowledge_path / path.name
        if not destination.exists():
            shutil.copy2(path, destination)
    store, engine = Store(data / "labagent.sqlite3"), Engine(settings)
    store.pause_running("服务已重新启动，实验已暂停；确认现场后可恢复。")
    knowledge = Knowledge(knowledge_path)
    gateway = gateway or Gateway(settings)
    agent = Agent(store, engine, knowledge, gateway, settings)
    perception = Perception(settings, gateway, engine.spec["required_objects"])

    @asynccontextmanager
    async def lifespan(_):
        yield
        gateway.close()

    app = FastAPI(title="LabAgent", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store, app.state.engine = store, engine
    app.state.agent, app.state.perception = agent, perception
    from .demo_api import mount_demo_api
    plans = mount_demo_api(app, settings, store, engine, knowledge, gateway)
    from .agent_api import mount_agent_api
    from .task_agent import TaskAgent
    controller = TaskAgent(settings,store,engine,knowledge,gateway,agent)
    app.state.task_agent = controller
    engine.task_agent = controller
    agent.task_agent = controller
    mount_agent_api(app,controller,store,engine)
    from .meter_live import mount_meter_live
    mount_meter_live(app,settings,store,engine,gateway)

    @app.exception_handler(DomainError)
    async def domain_error(_, error):
        return JSONResponse({"detail": error.message}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_, error):
        # Do not echo invalid values (including NaN/Inf or user secrets) into a JSON response.
        errors = [{"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
                  for item in error.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.netloc != request.headers.get("host") or parsed.scheme not in {"http", "https"}:
                    return JSONResponse({"detail": "禁止跨站访问本地实验服务"}, status_code=403)
            if settings.access_token:
                auth = request.headers.get("authorization", "")
                if not secrets.compare_digest(auth, "Bearer " + settings.access_token):
                    return JSONResponse({"detail": "需要正确的访问令牌"}, status_code=401)
            elif request.url.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
                return JSONResponse({"detail": "无令牌模式仅允许本机访问"}, status_code=403)
            if request.method == "POST":
                length = request.headers.get("content-length")
                if length is None:
                    return JSONResponse({"detail": "请求必须指定 Content-Length"}, status_code=411)
                if not length.isdecimal() or int(length) > 18_000_000:
                    return JSONResponse({"detail": "请求体过大"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "providers": settings.public(),
                "knowledge_chunks": len(knowledge.chunks),
                "capabilities":{"agent_controller":"llm","goal_planning":True,"document_planning":True,
                                "task_ui":"single-action-v2","equipment_verification":"independent-frames",
                                "experiment_report":"readable-eight-sections-v1",
                                "session_lifecycle":"exclusive-review-delete-v1",
                                "meter_live":"latest-frame-preview-commit-v1"}}

    @app.get("/api/experiment")
    def experiment():
        return engine.spec

    @app.get("/api/sessions")
    def sessions():
        return store.list()

    @app.post("/api/sessions", status_code=201)
    def new_session(body: NewSession):
        plan = plans.get(body.plan_id) if body.plan_id else None
        if body.automation=="agent" and not plan:
            raise DomainError("Agent实验需要先生成并审核实验计划",422)
        if plan and not plan.approved:
            raise DomainError("请先审核并启用实验计划", 409)
        return engine.view(store.create(body, plan.model_dump() if plan else None))

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        return engine.view(store.get(session_id))

    @app.post("/api/sessions/{session_id}/open")
    def open_session(session_id: str, body: SessionReview):
        return engine.view(store.review(session_id, body.previous_session_id))

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str, body: SessionDeletion):
        if not body.confirmed:
            raise DomainError("请明确确认删除该实验及其测量、对话和过程记录", 422)
        store.delete(session_id, body.expected_revision)
        controller.forget(session_id)
        app.state.meter_previews.forget(session_id)
        return {"deleted": True, "session_id": session_id}

    @app.post("/api/sessions/{session_id}/actions")
    def action(session_id: str, body: Action):
        if body.advance_after and (body.automatic or body.kind not in {
            "confirm_step", "read_sop", "confirm_meter_setup", "confirm_equipment",
            "confirm_candidate", "manual_measurement", "save_measurement",
        }):
            raise DomainError("仅本步明确确认可以合并继续操作", 422)
        if body.automatic and body.kind not in {"advance", "pause", "report", "save_measurement"}:
            raise DomainError("自动指导不能替代现场确认或写入未经核对的测量", 422)
        if body.automatic and body.kind == "save_measurement":
            from .guidance import decide
            decision = decide(engine, store.get(session_id))
            if not decision or decision["tool"] != "save_measurement":
                raise DomainError("当前步骤不允许自动保存", 409)
            body.confirmed = True
        def apply_action(current):
            result = engine.apply(current, body)
            if result["accepted"] and body.advance_after and current.plan and not engine.blockers(current):
                advanced = engine.apply(current, Action(kind="advance"))
                if advanced["accepted"]:
                    result = {**result, "advanced": True, "message": result["message"] + "；" + advanced["message"]}
            return result
        state, result = store.mutate(session_id, "agent.action" if body.automatic else "user.action", body.model_dump(),
                                     apply_action, body.expected_revision)
        return {**result, "session": engine.view(state)}

    @app.get("/api/sessions/{session_id}/messages")
    def messages(session_id: str):
        return store.messages(session_id)

    @app.post("/api/sessions/{session_id}/chat")
    def chat(session_id: str, body: ChatRequest):
        return agent.chat(session_id, body.message)

    @app.get("/api/sessions/{session_id}/events")
    def events(session_id: str, after: int = Query(default=0, ge=0)):
        return store.events(session_id, after)

    @app.get("/api/sessions/{session_id}/export")
    def export(session_id: str, format: Literal["md", "html", "json", "csv"] = "md"):
        state = store.get(session_id)
        all_events, last = [], 0
        while page := store.events(session_id, last, 1000):
            all_events.extend(page)
            last = page[-1]["sequence"]
        if format == "json":
            content = json.dumps({"session": engine.view(state), "events": all_events,
                                  "messages": store.messages(session_id, 100000)}, ensure_ascii=False, indent=2)
            media = "application/json"
        elif format == "csv":
            content, media = measurements_csv(state), "text/csv"
        elif format == "html":
            content, media = html_report(state, engine.spec_for(state), all_events,
                                        image_dir=settings.data_dir / "images"), "text/html"
        else:
            content, media = markdown_report(state, engine.spec_for(state), all_events), "text/markdown"
        return Response(content, media_type=media, headers={"Content-Disposition":
                        f'attachment; filename="labagent-{session_id}.{format}"'})

    @app.get("/api/knowledge/search")
    def search(q: str = Query(min_length=1, max_length=4000)):
        return {"results": knowledge.search(q)}

    @app.post("/api/knowledge/upload")
    async def upload_knowledge(file: UploadFile):
        raw = await file.read(1_000_001)
        await file.close()
        if len(raw) > 1_000_000:
            raise DomainError("知识库文件上限 1 MB", 413)
        try:
            return knowledge.ingest(file.filename or "manual.md", raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise DomainError("请上传 UTF-8 编码的 .md/.txt 文件", 422) from exc

    @app.get("/api/training-photos")
    def training_photo_count():
        return {"count": photo_count(data / "equipment-capture" / "raw")}

    @app.post("/api/training-photos", status_code=201)
    def upload_training_photo(file: Annotated[UploadFile, File()]):
        with file.file:
            raw = file.file.read(MAX_PHOTO_BYTES + 1)
        return save_photo(data / "equipment-capture" / "raw", raw)

    @app.get("/api/fixtures")
    def fixtures():
        return [{"id": p.stem, "name": json.loads(p.read_text(encoding="utf-8"))["name"]}
                for p in sorted((ROOT / "fixtures").glob("*.json"))]

    @app.post("/api/replays")
    def run_replay(body: ReplayRequest):
        return replay(store, engine, body)

    @app.post("/api/fixtures/{fixture_id}")
    def run_fixture(fixture_id: str):
        paths = {p.stem: p for p in (ROOT / "fixtures").glob("*.json")}
        if fixture_id not in paths:
            raise DomainError("测试剧本不存在", 404)
        return replay(store, engine, ReplayRequest.model_validate_json(paths[fixture_id].read_text(encoding="utf-8")))

    @app.post("/api/sessions/{session_id}/image")
    def analyze_image(session_id: str, file: Annotated[UploadFile, File()],
                      task: Annotated[Literal["objects", "ocr", "both"], Form()] = "objects",
                      roi: Annotated[str, Form()] = "",
                      meter_range: Annotated[str, Form()] = "",
                      meter_range_confirmed: Annotated[bool, Form()] = False):
        before = store.get(session_id)
        raw = file.file.read(8_000_001)
        file.file.close()
        if len(raw) > 8_000_000:
            raise DomainError("图片上限 8 MB", 413)
        image = load_image(raw)
        result = {}
        if task in {"objects", "both"}:
            result["detection"] = perception.detect(image)
        if task in {"ocr", "both"}:
            try:
                selected_roi = [float(v) for v in (roi or settings.meter_roi).split(",") if v.strip()]
            except ValueError as exc:
                raise DomainError("ROI 格式应为 x,y,width,height", 422) from exc
            result["reading"] = perception.read_voltage(image, selected_roi)
            if meter_range:
                if not meter_range_confirmed:
                    raise DomainError("请现场核对并确认万用表档位", 422)
                result["reading"] = apply_meter_range(result["reading"], meter_range)
        image_hash = hashlib.sha256(raw).hexdigest()
        image_directory = data / "images"
        image_directory.mkdir(exist_ok=True)
        image.save(image_directory / (image_hash + ".jpg"), format="JPEG", quality=90)
        reference = "image:" + image_hash

        def apply(current):
            operations = []
            if "detection" in result:
                operations.append(apply_detection(current, result["detection"]["objects"], engine, reference))
            if "reading" in result:
                reading = result["reading"]
                if reading.get("backend") == "qwen25-vl-mnn-cpu":
                    operations.append({"accepted": False, "message": "视觉模型结果仅供预览，请核对后手动输入"})
                elif reading["value"] is None:
                    operations.append({"accepted": False, "message": "读数不明确，未写入测量"})
                else:
                    operations.append(engine.add_measurement(current, reading["value"], "ocr",
                                                             reading["confidence"], reference))
            return {"accepted": all(x["accepted"] for x in operations), "operations": operations,
                    "message": "；".join(x["message"] for x in operations)}

        state, applied = store.mutate(session_id, "perception.image", {"reference": reference, **result},
                                      apply, before.revision)
        return {**applied, "perception": result, "session": engine.view(state)}

    @app.post("/api/sessions/{session_id}/sensor")
    def sensor(session_id: str, body: SerialReading):
        state, result = store.mutate(session_id, "sensor.voltage", body.model_dump(),
                                     lambda current: engine.add_measurement(current, body.value, "serial", 1,
                                                                            "device:" + body.device),
                                     body.expected_revision)
        return {**result, "session": engine.view(state)}

    @app.post("/api/audio/transcribe")
    def transcribe(file: Annotated[UploadFile, File()]):
        mime = (file.content_type or "").split(";")[0]
        if mime not in {"audio/webm", "audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "video/webm"}:
            raise DomainError("不支持此音频类型", 422)
        raw = file.file.read(16_000_001)
        file.file.close()
        if len(raw) > 16_000_000:
            raise DomainError("音频上限 16 MB", 413)
        # Transcription is editable text, never automatically executes experimental actions.
        return {"text": gateway.transcribe(raw, Path(file.filename or "recording.webm").name, mime)}

    web = Path(__file__).parent / "web"
    app.mount("/static", StaticFiles(directory=web), name="static")

    @app.get("/")
    def index():
        return FileResponse(web / "demo.html")

    return app
