import pytest
from fastapi.testclient import TestClient

from labagent.config import Settings
from labagent.main import create_app
from labagent.rules import Engine
from labagent.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def engine(settings):
    return Engine(settings)


@pytest.fixture
def store(settings):
    return Store(settings.data_dir / "test.sqlite3")


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        yield client


def measurement_stage(engine, state):
    from labagent.models import Action

    for action in [Action(kind="read_sop", confirmed=True), Action(kind="advance"),
                   Action(kind="confirm_power_off", confirmed=True), Action(kind="advance"),
                   Action(kind="confirm_equipment", objects=engine.spec["required_objects"], confirmed=True),
                   Action(kind="advance"), Action(kind="confirm_connected", confirmed=True), Action(kind="advance"),
                   Action(kind="confirm_wiring", confirmed=True), Action(kind="advance"),
                   Action(kind="confirm_power_on", confirmed=True), Action(kind="advance")]:
        assert engine.apply(state, action)["accepted"], action.kind
    assert state.step == 6
    return state
