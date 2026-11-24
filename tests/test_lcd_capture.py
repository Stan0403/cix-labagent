"""Camera-data integrity tests; all writes stay in temporary test directories."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import sys
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lcd_capture import create_app


@pytest.fixture
def capture_client(tmp_path):
    return TestClient(create_app(tmp_path / "captures"))


def jpeg():
    buffer = BytesIO()
    Image.new("RGB", (640, 480), "#75979b").save(buffer, "JPEG", quality=98)
    return buffer.getvalue()


def new_batch(client):
    response = client.post("/api/batches", data={"name": "manual test"})
    assert response.status_code == 201
    return response.json()["batch_id"]


def save(client, batch_id, raw=None, **fields):
    data = {"capture_id": uuid4().hex, "kind": "reading", "label": "-0.012", **fields}
    return client.post(
        f"/api/batches/{batch_id}/photos",
        data=data,
        files={"file": ("camera.jpg", jpeg() if raw is None else raw, "image/jpeg")},
    )


def test_full_frame_bytes_label_and_manifest(capture_client):
    client = capture_client
    batch_id = new_batch(client)
    raw = jpeg()
    result = save(client, batch_id, raw).json()
    assert (result["record"]["width"], result["record"]["height"]) == (640, 480)
    assert result["record"]["label"] == "-0.012"
    assert result["record"]["group"] == batch_id
    assert result["record"]["review_status"] == "pending"
    assert (Path(result["directory"]) / result["filename"]).read_bytes() == raw
    manifest = client.get(f"/api/batches/{batch_id}/manifest.jsonl")
    assert json.loads(manifest.text)["label"] == "-0.012"


@pytest.mark.parametrize("kind,label", [("unlabeled", None), ("unreadable", None), ("blank", "")])
def test_empty_and_unknown_are_distinct(capture_client, kind, label):
    result = save(capture_client, new_batch(capture_client), kind=kind, label="stale text").json()
    assert result["record"]["label"] == label
    assert result["record"]["kind"] == kind


@pytest.mark.parametrize("label", [".001", "00.3", "1359", "+0.01", "-0.00"])
def test_preserves_literal_numeric_string(capture_client, label):
    response = save(capture_client, new_batch(capture_client), label=label)
    assert response.status_code == 201
    assert response.json()["record"]["label"] == label


def test_overrange_is_not_an_accepted_numeric_reading(capture_client):
    response = save(capture_client, new_batch(capture_client), kind="overrange", label="OL")
    assert response.status_code == 201
    assert response.json()["record"]["kind"] == "overrange"


@pytest.mark.parametrize(
    "fields",
    [{"label": "1.3.53"}, {"label": "nan"}, {"label": "-"}, {"kind": "unknown"}, {"capture_id": "../bad"}],
)
def test_rejects_invalid_metadata_without_saving(capture_client, fields):
    batch_id = new_batch(capture_client)
    assert save(capture_client, batch_id, **fields).status_code == 422
    assert capture_client.get(f"/api/batches/{batch_id}").json()["count"] == 0


def test_bad_image_does_not_create_record(capture_client):
    batch_id = new_batch(capture_client)
    assert save(capture_client, batch_id, b"not-a-photo").status_code == 422
    assert capture_client.get(f"/api/batches/{batch_id}").json()["count"] == 0


def test_retry_does_not_duplicate_and_conflicting_retry_is_rejected(capture_client):
    batch_id = new_batch(capture_client)
    capture_id = uuid4().hex
    assert save(capture_client, batch_id, capture_id=capture_id).json()["count"] == 1
    assert save(capture_client, batch_id, capture_id=capture_id).json()["count"] == 1
    assert save(capture_client, batch_id, capture_id=capture_id, label="1.34").status_code == 409


def test_concurrent_photos_keep_separate_records(capture_client):
    batch_id = new_batch(capture_client)
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: save(capture_client, batch_id), range(6)))
    assert all(r.status_code == 201 for r in responses)
    assert capture_client.get(f"/api/batches/{batch_id}").json()["count"] == 6
    assert len(capture_client.get(f"/api/batches/{batch_id}/manifest.jsonl").text.splitlines()) == 6


def test_static_page_and_cross_origin_write(capture_client):
    assert capture_client.get("/").status_code == 200
    assert capture_client.get("/static/lcd-capture.js").status_code == 200
    assert capture_client.get("/static/not-allowed.txt").status_code == 404
    assert capture_client.post("/api/batches", headers={"Origin": "https://example.org"}).status_code == 403
    assert capture_client.post("/api/batches", headers={"Origin": "http://testserver"}).status_code == 201
