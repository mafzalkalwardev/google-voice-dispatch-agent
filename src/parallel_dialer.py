"""Fixed-slot contact queue for parallel Google Voice calling."""
from __future__ import annotations

import queue
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from src.system_profile import effective_requested_slots


@dataclass(frozen=True)
class ParallelSlotConfig:
    slot_id: int
    profile_name: str
    loopback_device: str
    capture_device: str
    account_id: str = ""
    account_email: str = ""


def build_parallel_slots(
    *,
    requested: int,
    max_concurrent_dials: int,
    force_requested_slots: bool,
    primary_profile: str,
    primary_loopback: str,
    primary_capture: str,
    parallel_profiles: list[dict[str, Any]] | None,
    allow_shared_audio_routes: bool = False,
) -> list[ParallelSlotConfig]:
    """Validate profile/audio isolation and return the effective worker slots."""
    capped = min(max(1, int(requested or 1)), max(1, int(max_concurrent_dials or 1)))
    effective = effective_requested_slots(
        capped,
        force_requested_slots=bool(force_requested_slots),
    )
    if effective == 1:
        return [ParallelSlotConfig(0, primary_profile, primary_loopback, primary_capture)]

    entries = list(parallel_profiles or [])
    if len(entries) < effective:
        raise ValueError(
            f"Parallel calling requested {effective} slots but parallel_profiles has "
            f"only {len(entries)} entries. Configure one Chrome profile and isolated "
            "loopback/capture route per slot."
        )

    slots: list[ParallelSlotConfig] = []
    for index, entry in enumerate(entries[:effective]):
        profile = str(entry.get("profile_name") or "").strip()
        loopback = str(entry.get("loopback_device") or "").strip()
        capture = str(entry.get("capture_device") or "").strip()
        if not profile or not loopback or not capture:
            raise ValueError(
                f"parallel_profiles[{index}] must define profile_name, "
                "loopback_device, and capture_device."
            )
        slots.append(ParallelSlotConfig(
            index, profile, loopback, capture,
            str(entry.get("account_id") or ""),
            str(entry.get("account_email") or ""),
        ))

    profiles = [slot.profile_name.casefold() for slot in slots]
    if len(profiles) != len(set(profiles)):
        raise ValueError("Parallel slots must use distinct Chrome profile names.")

    routes = [
        (slot.loopback_device.casefold(), slot.capture_device.casefold())
        for slot in slots
    ]
    if not allow_shared_audio_routes and len(routes) != len(set(routes)):
        raise ValueError(
            "Parallel AI calls must use distinct loopback/capture routes. "
            "Set allow_shared_audio_routes only for an explicitly controlled test."
        )
    return slots


class ParallelDialer:
    """Run one persistent worker per slot against a shared contact queue."""

    def __init__(
        self,
        slots: list[ParallelSlotConfig],
        *,
        cooldown_min: float = 0.0,
        cooldown_max: float = 0.0,
    ) -> None:
        if not slots:
            raise ValueError("At least one parallel dialer slot is required.")
        self.slots = list(slots)
        self.cooldown_min = max(0.0, float(cooldown_min))
        self.cooldown_max = max(self.cooldown_min, float(cooldown_max))

    def run(
        self,
        items: Iterable[tuple[int, Any]],
        *,
        context_factory: Callable[[ParallelSlotConfig], Any],
        process_item: Callable[[ParallelSlotConfig, Any, int, Any], Any],
        context_close: Callable[[ParallelSlotConfig, Any], None],
    ) -> list[Any]:
        work: queue.Queue[tuple[int, Any]] = queue.Queue()
        for index, item in items:
            work.put((index, item))

        results: list[Any] = []
        errors: list[BaseException] = []
        result_lock = threading.Lock()

        def slot_worker(slot: ParallelSlotConfig) -> None:
            context = None
            try:
                context = context_factory(slot)
                while True:
                    try:
                        index, item = work.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        result = process_item(slot, context, index, item)
                        with result_lock:
                            results.append(result)
                    finally:
                        work.task_done()
                    if not work.empty() and self.cooldown_max > 0:
                        time.sleep(random.uniform(self.cooldown_min, self.cooldown_max))
            except BaseException as exc:
                with result_lock:
                    errors.append(exc)
            finally:
                if context is not None:
                    context_close(slot, context)

        threads = [
            threading.Thread(
                target=slot_worker,
                args=(slot,),
                daemon=True,
                name=f"DialSlot-{slot.slot_id + 1}",
            )
            for slot in self.slots
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if errors:
            raise RuntimeError(f"Parallel dialer worker failed: {errors[0]}") from errors[0]
        return results
