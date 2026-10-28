from __future__ import annotations

import threading

import pytest

from src.parallel_dialer import ParallelDialer, ParallelSlotConfig, build_parallel_slots


def test_parallel_slots_require_a_profile_and_isolated_audio_per_line(monkeypatch):
    monkeypatch.setattr("src.system_profile.recommended_slots", lambda requested: requested)
    with pytest.raises(ValueError, match="parallel_profiles"):
        build_parallel_slots(
            requested=2,
            max_concurrent_dials=5,
            force_requested_slots=True,
            primary_profile="primary",
            primary_loopback="Cable A Input",
            primary_capture="Cable A Output",
            parallel_profiles=[],
        )

    with pytest.raises(ValueError, match="distinct loopback/capture"):
        build_parallel_slots(
            requested=2,
            max_concurrent_dials=5,
            force_requested_slots=True,
            primary_profile="primary",
            primary_loopback="Cable A Input",
            primary_capture="Cable A Output",
            parallel_profiles=[
                {"profile_name": "p1", "loopback_device": "A", "capture_device": "B"},
                {"profile_name": "p2", "loopback_device": "A", "capture_device": "B"},
            ],
        )


def test_parallel_dialer_uses_fixed_workers_and_processes_each_item_once():
    slots = [
        ParallelSlotConfig(0, "p1", "a-in", "a-out"),
        ParallelSlotConfig(1, "p2", "b-in", "b-out"),
    ]
    seen: list[tuple[int, int]] = []
    lock = threading.Lock()

    def process(slot, context, index, item):
        assert context == slot.profile_name
        with lock:
            seen.append((index, item))
        return item

    results = ParallelDialer(slots).run(
        list(enumerate(range(12), start=1)),
        context_factory=lambda slot: slot.profile_name,
        process_item=process,
        context_close=lambda slot, context: None,
    )

    assert sorted(results) == list(range(12))
    assert sorted(seen) == list(enumerate(range(12), start=1))
