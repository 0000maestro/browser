#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "uklok_automation_execute",
    ROOT / "automations" / "_runtime" / "execute.py",
)
assert SPEC is not None and SPEC.loader is not None
execute = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(execute)


def write_state(path: Path, value: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value) + "\n", encoding="utf-8")
    tmp.replace(path)


def state(
    *,
    latest_seq: int,
    lifecycle_seq: int,
    active: list[dict] | None = None,
    streams: list[dict] | None = None,
    counters: dict | None = None,
    target_counters: dict | None = None,
    target_lifecycle_seq: dict | None = None,
    recorder_pid: int | None = None,
) -> dict:
    return {
        "schema": "uklok.browser.traffic-state/v1",
        "recorder_pid": os.getpid() if recorder_pid is None else recorder_pid,
        "latest_seq": latest_seq,
        "lifecycle_seq": lifecycle_seq,
        "target_lifecycle_seq": target_lifecycle_seq or {},
        "counters": counters or {},
        "target_counters": target_counters or {},
        "active_requests": active or [],
        "open_streams": streams or [],
    }


now = time.time()
classified = execute._classify_traffic_state(
    state(
        latest_seq=7,
        lifecycle_seq=4,
        active=[
            {
                "key": "page:fetch",
                "request_id": "fetch",
                "target_id": "controlled",
                "type": "Fetch",
                "method": "GET",
                "url": "https://example.test/fetch",
                "started_at": now - 1,
            },
            {
                "key": "page:stale",
                "request_id": "stale",
                "target_id": "controlled",
                "type": "XHR",
                "method": "GET",
                "url": "https://example.test/stale",
                "started_at": now - 31,
            },
            {
                "key": "page:events",
                "request_id": "events",
                "target_id": "controlled",
                "type": "EventSource",
                "method": "GET",
                "url": "https://example.test/events",
                "started_at": now - 60,
            },
        ],
        streams=[
            {
                "key": "page:ws",
                "request_id": "ws",
                "target_id": "controlled",
                "type": "WebSocket",
                "url": "wss://example.test/socket",
                "started_at": now - 60,
            }
        ],
    ),
    now_epoch=now,
    stale_after_seconds=30,
    target_id="controlled",
)
assert [item["request_id"] for item in classified["blocking"]] == ["fetch"]
assert [item["request_id"] for item in classified["stale"]] == ["stale"]
assert {item["request_id"] for item in classified["persistent"]} == {"events", "ws"}
assert classified["carryover"] == []
assert execute._counter_delta(
    {"failed": 2, "http_error": 1},
    {"failed": 1, "http_error": 0},
) == {"failed": 1, "http_error": 1}

other_target = execute._classify_traffic_state(
    state(
        latest_seq=1,
        lifecycle_seq=1,
        active=[
            {
                "key": "other:req",
                "request_id": "other",
                "target_id": "other",
                "type": "Fetch",
                "method": "GET",
                "url": "https://other.test/",
                "started_at": now,
            }
        ],
    ),
    now_epoch=now,
    stale_after_seconds=30,
    target_id="controlled",
)
assert other_target["blocking"] == []
assert execute._traffic_lifecycle_seq(
    state(
        latest_seq=1,
        lifecycle_seq=99,
        target_lifecycle_seq={"controlled": 3, "other": 96},
    ),
    "controlled",
) == 3


with tempfile.TemporaryDirectory() as raw_tmp:
    tmp = Path(raw_tmp)
    state_path = tmp / "traffic-state.json"
    boundaries_path = tmp / "traffic-checkpoints.jsonl"
    payload = {
        "capture_traffic": True,
        "automation": "traffic_test",
        "run_dir": str(tmp),
        "traffic_state": str(state_path),
        "traffic_checkpoints": str(boundaries_path),
    }
    step = {"id": "go", "checkpoint": "The page transition is complete."}

    write_state(
        state_path,
        state(
            latest_seq=2,
            lifecycle_seq=1,
            active=[
                {
                    "key": "page:req",
                    "request_id": "req",
                    "type": "Document",
                    "method": "GET",
                    "url": "https://example.test/",
                    "started_at": time.time(),
                }
            ],
            counters={"started": 1, "finished": 0, "failed": 0, "http_error": 0, "redirected": 0},
        ),
    )

    def finish_request() -> None:
        time.sleep(0.05)
        write_state(
            state_path,
            state(
                latest_seq=4,
                lifecycle_seq=2,
                counters={"started": 1, "finished": 1, "failed": 0, "http_error": 0, "redirected": 0},
            ),
        )

    worker = threading.Thread(target=finish_request)
    worker.start()
    boundary, cursor = execute._traffic_boundary(
        payload=payload,
        step=step,
        step_status="passed",
        cursor={"seq": 0, "counters": {}},
        policy={
            "quiet_seconds": 0.03,
            "max_wait_seconds": 0.5,
            "stale_after_seconds": 1.0,
            "poll_seconds": 0.01,
        },
    )
    worker.join()

    assert boundary is not None
    assert boundary["settle"]["outcome"] == "quiet"
    assert boundary["from_seq"] == 1
    assert boundary["through_seq"] == 4
    assert boundary["traffic_delta"]["started"] == 1
    assert boundary["traffic_delta"]["finished"] == 1
    assert cursor["seq"] == 4
    persisted = [json.loads(line) for line in boundaries_path.read_text(encoding="utf-8").splitlines()]
    assert persisted[-1]["step"] == "go"

    write_state(
        state_path,
        state(
            latest_seq=5,
            lifecycle_seq=3,
            active=[
                {
                    "key": "page:busy",
                    "request_id": "busy",
                    "type": "Fetch",
                    "method": "POST",
                    "url": "https://example.test/busy",
                    "started_at": time.time(),
                }
            ],
            counters={"started": 2, "finished": 1, "failed": 0, "http_error": 0, "redirected": 0},
        ),
    )
    busy, _ = execute._traffic_boundary(
        payload=payload,
        step=step,
        step_status="passed",
        cursor=cursor,
        policy={
            "quiet_seconds": 0.01,
            "max_wait_seconds": 0.03,
            "stale_after_seconds": 10.0,
            "poll_seconds": 0.005,
        },
    )
    assert busy is not None
    assert busy["settle"]["outcome"] == "timeout_busy"
    assert busy["settle"]["blocking"][0]["request_id"] == "busy"

    busy_cursor = {
        "seq": busy["through_seq"],
        "target_counters": {},
        "counters": {"started": 2, "finished": 1, "failed": 0, "http_error": 0, "redirected": 0},
        "carryover_keys": ["page:busy"],
    }
    carried, _ = execute._traffic_boundary(
        payload=payload,
        step=step,
        step_status="passed",
        cursor=busy_cursor,
        policy={
            "quiet_seconds": 0.01,
            "max_wait_seconds": 0.03,
            "stale_after_seconds": 10.0,
            "poll_seconds": 0.005,
        },
    )
    assert carried is not None
    assert carried["settle"]["outcome"] == "quiet_with_carryover"
    assert carried["settle"]["blocking"] == []
    assert carried["settle"]["carryover"][0]["request_id"] == "busy"

    write_state(
        state_path,
        state(
            latest_seq=5,
            lifecycle_seq=3,
            recorder_pid=-1,
        ),
    )
    unavailable, _ = execute._traffic_boundary(
        payload=payload,
        step=step,
        step_status="passed",
        cursor=cursor,
        policy={
            "quiet_seconds": 0.01,
            "max_wait_seconds": 0.05,
            "stale_after_seconds": 1.0,
            "poll_seconds": 0.005,
        },
    )
    assert unavailable is not None
    assert unavailable["settle"]["outcome"] == "unavailable"


print("traffic boundary validation passed")
