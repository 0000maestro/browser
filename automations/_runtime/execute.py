from __future__ import annotations

import datetime as dt
import json
import os
import time
import tomllib
import traceback
from pathlib import Path
from typing import Any


TRAFFIC_POLICY = {
    "quiet_seconds": 0.75,
    "max_wait_seconds": 10.0,
    "stale_after_seconds": 30.0,
    "poll_seconds": 0.1,
}
PERSISTENT_RESOURCE_TYPES = {"EventSource"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _append_event(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True, default=str) + "\n")


def _step_workspace(run_root: Path, ordinal: int, step_id: str) -> Path:
    path = run_root / "steps" / f"{ordinal:02d}-{step_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _artifact_path(ctx: dict[str, Any], name: str | Path) -> Path:
    raw = Path(name)
    if raw.is_absolute():
        raw = Path(raw.name)
    if raw.name in {"", ".", ".."}:
        raise ValueError(f"invalid step artifact path: {name!r}")

    step_dir_value = ctx.get("step_dir")
    if not step_dir_value:
        raise RuntimeError("step artifact requested outside an active checkpoint")

    step_dir = Path(step_dir_value).resolve()
    target = (step_dir / raw).resolve()
    if target != step_dir and step_dir not in target.parents:
        raise ValueError(f"step artifact escaped checkpoint directory: {name!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _checkpoint_record(
    *,
    path: Path,
    payload: dict[str, Any],
    step: dict[str, Any],
    ordinal: int,
    status: str,
    observed: Any = None,
    evidence: Any = None,
    error: str | None = None,
    traffic_boundary: dict[str, Any] | None = None,
) -> None:
    _write_json(
        path,
        {
            "schema": "uklok.browser.automation-checkpoint/v1",
            "automation": payload["automation"],
            "run_id": Path(payload["run_dir"]).name,
            "ordinal": ordinal,
            "step": step["id"],
            "status": status,
            "expected": step["checkpoint"],
            "at": _now(),
            "observed": _safe(observed),
            "evidence": _safe(evidence),
            "error": error,
            "traffic_boundary": _safe(traffic_boundary),
        },
    )


def _browser_snapshot(
    browser: dict[str, Any],
    capture_screenshot: bool,
    step_dir: Path | None = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}

    current_tab = browser.get("current_tab")
    if callable(current_tab):
        try:
            snapshot["tab"] = _safe(current_tab())
        except Exception as exc:
            snapshot["tab_error"] = str(exc)

    page_info = browser.get("page_info")
    if callable(page_info):
        try:
            snapshot["page_info"] = _safe(page_info())
        except Exception as exc:
            snapshot["page_info_error"] = str(exc)

    if capture_screenshot:
        screenshot = browser.get("capture_screenshot")
        if callable(screenshot):
            try:
                target = step_dir / "failure.png" if step_dir is not None else None
                snapshot["screenshot"] = str(
                    screenshot(str(target), full=False) if target is not None else screenshot(full=False)
                )
            except Exception as exc:
                snapshot["screenshot_error"] = str(exc)

    return snapshot


def _load_module(module_path: Path, browser: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    namespace: dict[str, Any] = {
        "__builtins__": __builtins__,
        "__file__": str(module_path),
        "__name__": f"uklok_automation_{ctx['automation']}",
        "ctx": ctx,
        "artifact_path": lambda name: str(_artifact_path(ctx, name)),
    }
    for name, value in browser.items():
        if not name.startswith("__"):
            namespace[name] = value

    screenshot = browser.get("capture_screenshot")
    if callable(screenshot):
        def capture_step_screenshot(path: str | Path | None = None, *args: Any, **kwargs: Any) -> Any:
            if path is None:
                counter = int(ctx.get("_screenshot_counter", 0)) + 1
                ctx["_screenshot_counter"] = counter
                target = _artifact_path(ctx, f"screenshot-{counter:02d}.png")
            else:
                target = _artifact_path(ctx, path)
            return screenshot(str(target), *args, **kwargs)

        namespace["capture_screenshot"] = capture_step_screenshot

    source = module_path.read_text(encoding="utf-8")
    exec(compile(source, str(module_path), "exec"), namespace, namespace)
    return namespace


def _read_traffic_state(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"traffic state is unavailable: {path}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"traffic state could not be read: {exc}"



def _process_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

def _traffic_item(value: dict[str, Any], now_epoch: float) -> dict[str, Any]:
    started_at = value.get("started_at")
    age_seconds = max(0.0, now_epoch - started_at) if isinstance(started_at, (int, float)) else None
    item = {
        "key": value.get("key"),
        "request_id": value.get("request_id"),
        "target_id": value.get("target_id"),
        "type": value.get("type"),
        "method": value.get("method"),
        "url": value.get("url"),
        "response_status": value.get("response_status"),
    }
    if age_seconds is not None:
        item["age_ms"] = round(age_seconds * 1000)
    return item


def _classify_traffic_state(
    state: dict[str, Any],
    *,
    now_epoch: float,
    stale_after_seconds: float,
    target_id: str | None = None,
    carryover_keys: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    blocking: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    persistent: list[dict[str, Any]] = []
    carryover: list[dict[str, Any]] = []
    carried = carryover_keys or set()

    for request in state.get("active_requests", []):
        if target_id and request.get("target_id") != target_id:
            continue
        item = _traffic_item(request, now_epoch)
        if request.get("type") in PERSISTENT_RESOURCE_TYPES:
            persistent.append(item)
            continue
        if request.get("key") in carried:
            carryover.append(item)
            continue

        started_at = request.get("started_at")
        age_seconds = max(0.0, now_epoch - started_at) if isinstance(started_at, (int, float)) else 0.0
        if age_seconds >= stale_after_seconds:
            stale.append(item)
        else:
            blocking.append(item)

    for stream in state.get("open_streams", []):
        if target_id and stream.get("target_id") != target_id:
            continue
        persistent.append(_traffic_item(stream, now_epoch))

    return {
        "blocking": blocking,
        "stale": stale,
        "persistent": persistent,
        "carryover": carryover,
    }



def _traffic_target_id(browser: dict[str, Any]) -> str | None:
    current_tab = browser.get("current_tab")
    if not callable(current_tab):
        return None
    try:
        tab = current_tab()
    except Exception:
        return None
    if not isinstance(tab, dict):
        return None
    value = tab.get("targetId") or tab.get("target_id")
    return str(value) if value else None



def _traffic_lifecycle_seq(state: dict[str, Any], target_id: str | None) -> int:
    if target_id:
        target_values = state.get("target_lifecycle_seq") or {}
        return int(target_values.get(target_id, 0))
    return int(state.get("lifecycle_seq", 0))


def _traffic_counters(state: dict[str, Any], target_id: str | None) -> dict[str, Any]:
    if target_id:
        target_values = state.get("target_counters") or {}
        return dict(target_values.get(target_id) or {})
    return dict(state.get("counters") or {})


def _traffic_cursor(payload: dict[str, Any]) -> dict[str, Any]:
    cursor = {"seq": 0, "target_counters": {}, "carryover_keys": []}
    if not payload.get("capture_traffic"):
        return cursor

    state_path = payload.get("traffic_state")
    if not state_path:
        return cursor

    state, _ = _read_traffic_state(Path(state_path))
    if state is None:
        return cursor

    return {
        "seq": int(state.get("latest_seq", 0)),
        "target_counters": {
            key: dict(value)
            for key, value in (state.get("target_counters") or {}).items()
        },
        "counters": dict(state.get("counters") or {}),
        "carryover_keys": [],
    }


def _counter_delta(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, int]:
    keys = set(current) | set(previous)
    delta: dict[str, int] = {}
    for key in sorted(keys):
        try:
            delta[key] = max(0, int(current.get(key, 0)) - int(previous.get(key, 0)))
        except (TypeError, ValueError):
            continue
    return delta


def _traffic_boundary(
    *,
    payload: dict[str, Any],
    step: dict[str, Any],
    step_status: str,
    cursor: dict[str, Any],
    target_id: str | None = None,
    policy: dict[str, float] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not payload.get("capture_traffic"):
        return None, cursor

    settings = dict(TRAFFIC_POLICY)
    if policy:
        settings.update(policy)

    boundary_path_value = payload.get("traffic_checkpoints")
    state_path_value = payload.get("traffic_state")
    if not boundary_path_value or not state_path_value:
        return None, cursor

    boundary_path = Path(boundary_path_value)
    state_path = Path(state_path_value)
    started = time.monotonic()
    quiet_since: float | None = None
    last_lifecycle: int | None = None
    final_state: dict[str, Any] | None = None
    final_classification = {"blocking": [], "stale": [], "persistent": [], "carryover": []}
    outcome = "unavailable"
    error: str | None = None

    try:
        while True:
            state, state_error = _read_traffic_state(state_path)
            if state is None:
                error = state_error
                outcome = "unavailable"
                break

            final_state = state
            if not _process_alive(state.get("recorder_pid")):
                error = "traffic recorder is no longer running"
                outcome = "unavailable"
                break

            now_epoch = time.time()
            final_classification = _classify_traffic_state(
                state,
                now_epoch=now_epoch,
                stale_after_seconds=settings["stale_after_seconds"],
                target_id=target_id,
                carryover_keys=set(cursor.get("carryover_keys") or []),
            )
            lifecycle = _traffic_lifecycle_seq(state, target_id)
            now_mono = time.monotonic()

            if final_classification["blocking"]:
                quiet_since = None
            else:
                if quiet_since is None or lifecycle != last_lifecycle:
                    quiet_since = now_mono
                if now_mono - quiet_since >= settings["quiet_seconds"]:
                    outcome = (
                        "quiet_with_carryover"
                        if (
                            final_classification["stale"]
                            or final_classification["persistent"]
                            or final_classification["carryover"]
                        )
                        else "quiet"
                    )
                    break

            if now_mono - started >= settings["max_wait_seconds"]:
                outcome = "timeout_busy"
                break

            last_lifecycle = lifecycle
            time.sleep(settings["poll_seconds"])
    except Exception as exc:
        outcome = "error"
        error = f"{type(exc).__name__}: {exc}"

    current_seq = cursor.get("seq", 0)
    previous_target_counters = dict((cursor.get("target_counters") or {}).get(target_id) or {}) if target_id else dict(cursor.get("counters") or {})
    current_counters: dict[str, Any] = previous_target_counters
    current_target_counters = dict(cursor.get("target_counters") or {})
    global_counters = dict(cursor.get("counters") or {})
    lifecycle_seq = None
    if final_state is not None:
        current_seq = int(final_state.get("latest_seq", current_seq))
        current_counters = _traffic_counters(final_state, target_id)
        current_target_counters = {
            key: dict(value)
            for key, value in (final_state.get("target_counters") or {}).items()
        }
        global_counters = dict(final_state.get("counters") or {})
        lifecycle_seq = _traffic_lifecycle_seq(final_state, target_id)

    carryover_keys = set(cursor.get("carryover_keys") or [])
    if final_state is not None:
        active_keys = {
            str(item.get("key"))
            for item in final_state.get("active_requests", [])
            if item.get("key")
        }
        carryover_keys.intersection_update(active_keys)
    if outcome == "timeout_busy":
        carryover_keys.update(
            str(item["key"])
            for item in final_classification["blocking"]
            if item.get("key")
        )

    boundary = {
        "schema": "uklok.browser.traffic-checkpoint/v1",
        "automation": payload["automation"],
        "run_id": Path(payload["run_dir"]).name,
        "step": step["id"],
        "checkpoint": step["checkpoint"],
        "step_status": step_status,
        "target_id": target_id,
        "at": _now(),
        "from_seq": int(cursor.get("seq", 0)) + 1,
        "through_seq": current_seq,
        "traffic_delta": _counter_delta(current_counters, previous_target_counters),
        "settle": {
            "outcome": outcome,
            "waited_ms": round((time.monotonic() - started) * 1000),
            "quiet_window_ms": round(settings["quiet_seconds"] * 1000),
            "max_wait_ms": round(settings["max_wait_seconds"] * 1000),
            "stale_after_ms": round(settings["stale_after_seconds"] * 1000),
            "lifecycle_seq": lifecycle_seq,
            "blocking": final_classification["blocking"],
            "stale": final_classification["stale"],
            "persistent": final_classification["persistent"],
            "carryover": final_classification["carryover"],
            "error": error,
        },
    }

    try:
        boundary_path.parent.mkdir(parents=True, exist_ok=True)
        _append_event(boundary_path, boundary)
    except Exception as exc:
        boundary["settle"]["outcome"] = "error"
        boundary["settle"]["error"] = f"could not persist traffic boundary: {type(exc).__name__}: {exc}"

    return boundary, {
        "seq": current_seq,
        "target_counters": current_target_counters,
        "counters": global_counters,
        "carryover_keys": sorted(carryover_keys),
    }


def _failure(
    *,
    payload: dict[str, Any],
    contract: dict[str, Any],
    step: dict[str, Any],
    failure_type: str,
    observed: Any,
    error: str | None,
    browser: dict[str, Any],
    traffic_boundary: dict[str, Any] | None,
    step_dir: Path,
) -> Path:
    run_dir = Path(payload["run_dir"])
    failure_policy = contract.get("failure", {})
    browser_state = _browser_snapshot(
        browser,
        bool(failure_policy.get("capture_screenshot", True)),
        step_dir,
    )
    step_id = step["id"]
    resume = step["resume"]

    failure = {
        "schema": "uklok.browser.automation-failure/v1",
        "automation": payload["automation"],
        "run_id": run_dir.name,
        "type": failure_type,
        "failed_at": _now(),
        "checkpoint": step_id,
        "expected": step["checkpoint"],
        "pseudocode": step["pseudocode"],
        "known_variants": step.get("known_variants", []),
        "on_failure": step["on_failure"],
        "resume": resume,
        "observed": _safe(observed),
        "error": error,
        "browser": browser_state,
        "contract": str(Path(payload["contract_path"]).relative_to(payload["root"])),
        "module": str(Path(payload["module_path"]).relative_to(payload["root"])),
        "function": f"step_{step_id}",
        "step_dir": str(step_dir.relative_to(run_dir)),
        "inputs": payload["recorded_inputs"],
        "steps": str((run_dir / "steps").relative_to(run_dir)),
        "traffic": payload.get("traffic"),
        "traffic_checkpoints": payload.get("traffic_checkpoints"),
        "traffic_checkpoint": _safe(traffic_boundary),
        "agent_entrypoint": failure_policy.get("agent_skill", ".agents/skills/browser-automation/SKILL.md"),
        "repair_scope": {
            "start_here": [
                "this failure.json",
                "the failed [[algorithm]] step in contract.toml",
                f"step_{step_id}() in run.py",
            ],
            "rerun": (
                f"./bin/browser-automation run {payload['automation']} --from {step_id}"
                if resume == "safe"
                else f"./bin/browser-automation run {payload['automation']}"
            ),
        },
    }
    path = run_dir / "failure.json"
    _write_json(path, failure)
    return path


def execute_from_env(browser: dict[str, Any]) -> None:
    raw = os.environ.get("UKLOK_AUTOMATION_PAYLOAD")
    if not raw:
        raise RuntimeError("UKLOK_AUTOMATION_PAYLOAD is missing")
    payload = json.loads(raw)

    contract_path = Path(payload["contract_path"])
    module_path = Path(payload["module_path"])
    run_dir = Path(payload["run_dir"])
    contract = tomllib.loads(contract_path.read_text(encoding="utf-8"))
    all_steps: list[dict[str, Any]] = contract["algorithm"]
    step_ordinals = {step["id"]: index for index, step in enumerate(all_steps, start=1)}
    steps = all_steps

    from_step = payload.get("from_step")
    if from_step:
        index = next(index for index, step in enumerate(steps) if step["id"] == from_step)
        steps = steps[index:]

    ctx: dict[str, Any] = {
        "automation": payload["automation"],
        "inputs": payload["inputs"],
        "run_root": str(run_dir),
        "run_dir": str(run_dir),
        "step_dir": None,
        "contract": {
            "id": contract["id"],
            "version": contract["version"],
            "intent": contract["intent"],
            "authority": contract["authority"],
        },
        "step": None,
    }

    namespace = _load_module(module_path, browser, ctx)
    events_path = run_dir / "events.ndjson"
    completed: list[str] = []
    traffic_cursor = _traffic_cursor(payload)
    traffic_boundaries: list[dict[str, Any]] = []

    for step in steps:
        step_id = step["id"]
        ordinal = step_ordinals[step_id]
        step_dir = _step_workspace(run_dir, ordinal, step_id)
        checkpoint_path = step_dir / "checkpoint.json"
        ctx["step"] = step
        ctx["step_dir"] = str(step_dir)
        ctx["run_dir"] = str(step_dir)
        ctx["_screenshot_counter"] = 0
        _checkpoint_record(
            path=checkpoint_path,
            payload=payload,
            step=step,
            ordinal=ordinal,
            status="running",
        )
        _write_json(
            run_dir / "state.json",
            {
                "automation": payload["automation"],
                "run_id": run_dir.name,
                "status": "running",
                "current_step": step_id,
                "current_step_dir": str(step_dir.relative_to(run_dir)),
                "completed": completed,
                "updated_at": _now(),
            },
        )
        _append_event(
            events_path,
            {
                "event": "step_started",
                "step": step_id,
                "at": _now(),
                "checkpoint": step["checkpoint"],
                "step_dir": str(step_dir.relative_to(run_dir)),
                "traffic_from_seq": traffic_cursor["seq"] + 1 if payload.get("capture_traffic") else None,
            },
        )

        fn = namespace[f"step_{step_id}"]
        try:
            result = fn(ctx)
        except Exception:
            error = traceback.format_exc()
            traffic_boundary, traffic_cursor = _traffic_boundary(
                payload=payload,
                step=step,
                step_status="exception",
                cursor=traffic_cursor,
                target_id=_traffic_target_id(browser),
            )
            if traffic_boundary:
                traffic_boundaries.append(traffic_boundary)
            path = _failure(
                payload=payload,
                contract=contract,
                step=step,
                failure_type="exception",
                observed=None,
                error=error,
                browser=browser,
                traffic_boundary=traffic_boundary,
                step_dir=step_dir,
            )
            _checkpoint_record(
                path=checkpoint_path,
                payload=payload,
                step=step,
                ordinal=ordinal,
                status="exception",
                error=error,
                traffic_boundary=traffic_boundary,
            )
            _append_event(
                events_path,
                {
                    "event": "step_failed",
                    "step": step_id,
                    "at": _now(),
                    "failure": str(path),
                    "traffic_boundary": _safe(traffic_boundary),
                },
            )
            raise RuntimeError(f"automation failed at {step_id}; handoff: {path}") from None

        if not isinstance(result, dict) or result.get("ok") is not True:
            observed = result.get("observed") if isinstance(result, dict) else result
            traffic_boundary, traffic_cursor = _traffic_boundary(
                payload=payload,
                step=step,
                step_status="checkpoint_failed",
                cursor=traffic_cursor,
                target_id=_traffic_target_id(browser),
            )
            if traffic_boundary:
                traffic_boundaries.append(traffic_boundary)
            path = _failure(
                payload=payload,
                contract=contract,
                step=step,
                failure_type="checkpoint",
                observed=observed,
                error=None,
                browser=browser,
                traffic_boundary=traffic_boundary,
                step_dir=step_dir,
            )
            _checkpoint_record(
                path=checkpoint_path,
                payload=payload,
                step=step,
                ordinal=ordinal,
                status="failed",
                observed=observed,
                traffic_boundary=traffic_boundary,
            )
            _append_event(
                events_path,
                {
                    "event": "checkpoint_failed",
                    "step": step_id,
                    "at": _now(),
                    "failure": str(path),
                    "traffic_boundary": _safe(traffic_boundary),
                },
            )
            raise RuntimeError(f"checkpoint failed at {step_id}; handoff: {path}")

        traffic_boundary, traffic_cursor = _traffic_boundary(
            payload=payload,
            step=step,
            step_status="passed",
            cursor=traffic_cursor,
            target_id=_traffic_target_id(browser),
        )
        if traffic_boundary:
            traffic_boundaries.append(traffic_boundary)

        _checkpoint_record(
            path=checkpoint_path,
            payload=payload,
            step=step,
            ordinal=ordinal,
            status="passed",
            observed=result.get("observed"),
            evidence=result.get("evidence"),
            traffic_boundary=traffic_boundary,
        )
        completed.append(step_id)
        _append_event(
            events_path,
            {
                "event": "checkpoint_passed",
                "step": step_id,
                "at": _now(),
                "observed": _safe(result.get("observed")),
                "evidence": _safe(result.get("evidence")),
                "traffic_boundary": _safe(traffic_boundary),
            },
        )

    degraded_outcomes = {"timeout_busy", "unavailable", "error"}
    result = {
        "schema": "uklok.browser.automation-result/v1",
        "automation": payload["automation"],
        "run_id": run_dir.name,
        "status": "succeeded",
        "completed_at": _now(),
        "completed_checkpoints": completed,
        "success_condition": contract["success"]["condition"],
        "steps": str((run_dir / "steps").relative_to(run_dir)),
        "traffic": payload.get("traffic"),
        "traffic_checkpoints": payload.get("traffic_checkpoints"),
        "traffic_observability": {
            "requested": bool(payload.get("capture_traffic")),
            "boundaries": len(traffic_boundaries),
            "degraded": any(
                boundary.get("settle", {}).get("outcome") in degraded_outcomes
                for boundary in traffic_boundaries
            ),
        },
    }
    _write_json(run_dir / "result.json", result)
    _write_json(
        run_dir / "state.json",
        {
            "automation": payload["automation"],
            "run_id": run_dir.name,
            "status": "succeeded",
            "current_step": None,
            "completed": completed,
            "updated_at": _now(),
        },
    )
