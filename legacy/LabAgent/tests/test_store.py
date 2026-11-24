from concurrent.futures import ThreadPoolExecutor

import pytest

from labagent.models import DomainError, NewSession


def test_rolls_back_failed_transaction(store):
    s = store.create(NewSession())
    def fail(state):
        state.step = 8
        raise RuntimeError("stop")
    with pytest.raises(RuntimeError):
        store.mutate(s.id, "failed", {}, fail)
    assert store.get(s.id).step == 0 and len(store.events(s.id)) == 1


def test_concurrent_revision_only_one_wins(store):
    s = store.create(NewSession())
    def change(_):
        try:
            store.mutate(s.id, "concurrent", {}, lambda state: {"accepted": True}, expected_revision=0)
            return "ok"
        except DomainError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(change, range(4)))
    assert results.count("ok") == 1 and results.count("conflict") == 3
    assert store.get(s.id).revision == 1


def test_events_paginate_without_loss(store):
    sid = store.create(NewSession()).id
    for i in range(5):
        store.audit(sid, "test", {"n": i})
    first = store.events(sid, limit=3)
    second = store.events(sid, after=first[-1]["sequence"], limit=3)
    assert len(first) + len(second) == 6 and first[-1]["sequence"] < second[0]["sequence"]
