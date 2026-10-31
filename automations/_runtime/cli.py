from __future__ import annotations

import argparse
import ast
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AUTOMATIONS = ROOT / "automations"
RUNS = ROOT / ".runs"
TEMPLATE = AUTOMATIONS / "_template"
SCHEMA = "uklok.browser.automation/v1"
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ContractError(RuntimeError):
    pass


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contract_dir(name: str) -> Path:
    if not ID_RE.fullmatch(name):
        raise ContractError(f"invalid automation id: {name!r}")
    path = (AUTOMATIONS / name).resolve()
    if path.parent != AUTOMATIONS.resolve():
        raise ContractError("automation path escaped automations/")
    return path


def _load_contract(name: str) -> tuple[Path, dict[str, Any]]:
    directory = _contract_dir(name)
    path = directory / "contract.toml"
    if not path.is_file():
        raise ContractError(f"missing contract: {path.relative_to(ROOT)}")
    try:
        contract = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ContractError(f"{path.relative_to(ROOT)}: invalid TOML: {exc}") from exc
    return path, contract


def _module_path(contract_path: Path, contract: dict[str, Any]) -> Path:
    module = contract.get("module")
    if not isinstance(module, str) or not module:
        raise ContractError("contract.module must name the implementation module")
    path = (contract_path.parent / module).resolve()
    if path.parent != contract_path.parent.resolve():
        raise ContractError("contract.module must stay inside its automation directory")
    if not path.is_file():
        raise ContractError(f"missing module: {path.relative_to(ROOT)}")
    return path


def _step_function(step_id: str) -> str:
    return f"step_{step_id}"


def validate(name: str) -> dict[str, Any]:
    contract_path, contract = _load_contract(name)
    errors: list[str] = []

    if contract.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA!r}")
    if contract.get("id") != name:
        errors.append(f"id must match directory name {name!r}")
    if contract.get("status") not in {"draft", "ready"}:
        errors.append("status must be 'draft' or 'ready'")
    if not isinstance(contract.get("version"), int) or contract["version"] < 1:
        errors.append("version must be a positive integer")
    if not isinstance(contract.get("intent"), str) or not contract["intent"].strip():
        errors.append("intent is required")

    authority = contract.get("authority")
    if not isinstance(authority, dict):
        errors.append("[authority] is required")
    else:
        if not isinstance(authority.get("allowed"), list):
            errors.append("authority.allowed must be an array")
        if not isinstance(authority.get("forbidden"), list):
            errors.append("authority.forbidden must be an array")

    success = contract.get("success")
    if not isinstance(success, dict) or not isinstance(success.get("condition"), str) or not success["condition"].strip():
        errors.append("[success].condition is required")

    failure = contract.get("failure")
    if not isinstance(failure, dict) or failure.get("agent_skill") != ".agents/skills/browser-automation/SKILL.md":
        errors.append("[failure].agent_skill must point to .agents/skills/browser-automation/SKILL.md")

    input_defs = contract.get("inputs", [])
    if not isinstance(input_defs, list):
        errors.append("[[inputs]] entries must form an array")
        input_defs = []
    input_names: set[str] = set()
    for item in input_defs:
        if not isinstance(item, dict):
            errors.append("each input must be a table")
            continue
        input_name = item.get("name")
        if not isinstance(input_name, str) or not ID_RE.fullmatch(input_name):
            errors.append(f"invalid input name: {input_name!r}")
            continue
        if input_name in input_names:
            errors.append(f"duplicate input: {input_name}")
        input_names.add(input_name)
        if item.get("type") not in {"string", "url", "int", "float", "bool"}:
            errors.append(f"input {input_name}: unsupported type")
        if not isinstance(item.get("required", False), bool):
            errors.append(f"input {input_name}: required must be boolean")
        if not isinstance(item.get("sensitive", False), bool):
            errors.append(f"input {input_name}: sensitive must be boolean")

    algorithm = contract.get("algorithm")
    if not isinstance(algorithm, list) or not algorithm:
        errors.append("at least one [[algorithm]] step is required")
        algorithm = []

    step_ids: list[str] = []
    for step in algorithm:
        if not isinstance(step, dict):
            errors.append("each algorithm step must be a table")
            continue
        step_id = step.get("id")
        if not isinstance(step_id, str) or not ID_RE.fullmatch(step_id):
            errors.append(f"invalid algorithm step id: {step_id!r}")
            continue
        if step_id in step_ids:
            errors.append(f"duplicate algorithm step id: {step_id}")
        step_ids.append(step_id)
        for field in ("pseudocode", "checkpoint", "on_failure"):
            if not isinstance(step.get(field), str) or not step[field].strip():
                errors.append(f"step {step_id}: {field} is required")
        if step.get("resume") not in {"safe", "restart"}:
            errors.append(f"step {step_id}: resume must be 'safe' or 'restart'")
        if "known_variants" in step and not isinstance(step["known_variants"], list):
            errors.append(f"step {step_id}: known_variants must be an array")

    module_path: Path | None = None
    try:
        module_path = _module_path(contract_path, contract)
    except ContractError as exc:
        errors.append(str(exc))

    if module_path is not None:
        try:
            tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        except SyntaxError as exc:
            errors.append(f"{module_path.relative_to(ROOT)}: invalid Python: {exc}")
        else:
            implemented = {
                node.name.removeprefix("step_")
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name.startswith("step_")
            }
            async_steps = {
                node.name
                for node in tree.body
                if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("step_")
            }
            if async_steps:
                errors.append(f"step functions must be synchronous: {sorted(async_steps)}")
            expected = set(step_ids)
            missing = expected - implemented
            extra = implemented - expected
            if missing:
                errors.append(f"module missing step functions: {sorted(_step_function(x) for x in missing)}")
            if extra:
                errors.append(f"module has steps absent from contract: {sorted(_step_function(x) for x in extra)}")

    if errors:
        raise ContractError("\n".join(f"- {error}" for error in errors))

    return {
        "id": name,
        "status": contract["status"],
        "intent": contract["intent"],
        "contract": str(contract_path.relative_to(ROOT)),
        "module": str(_module_path(contract_path, contract).relative_to(ROOT)),
        "steps": step_ids,
    }


def _automation_names() -> list[str]:
    return sorted(
        path.name
        for path in AUTOMATIONS.iterdir()
        if path.is_dir() and not path.name.startswith("_") and (path / "contract.toml").is_file()
    )


def validate_many(name: str | None) -> int:
    names = [name] if name else _automation_names()
    if not names:
        print("No tracked automations.")
        return 0
    failed = False
    for automation in names:
        try:
            summary = validate(automation)
        except ContractError as exc:
            failed = True
            print(f"FAIL {automation}\n{exc}", file=sys.stderr)
        else:
            print(f"OK   {automation} [{summary['status']}] {len(summary['steps'])} step(s)")
    return 1 if failed else 0


def list_automations() -> int:
    names = _automation_names()
    if not names:
        print("No tracked automations.")
        return 0
    for name in names:
        try:
            summary = validate(name)
            print(f"{name}\t{summary['status']}\t{summary['intent']}")
        except ContractError as exc:
            print(f"{name}\tINVALID\t{str(exc).splitlines()[0]}")
    return 0


def new_automation(name: str) -> int:
    target = _contract_dir(name)
    if target.exists():
        raise ContractError(f"automation already exists: {target.relative_to(ROOT)}")
    shutil.copytree(TEMPLATE, target)
    contract = target / "contract.toml"
    contract.write_text(contract.read_text(encoding="utf-8").replace('id = "replace_me"', f'id = "{name}"', 1), encoding="utf-8")
    print(f"Created {target.relative_to(ROOT)}")
    print("Next: write the contract intent/pseudocode first, then make run.py reflect it.")
    return 0


def _coerce(raw: str, kind: str) -> Any:
    if kind in {"string", "url"}:
        return raw
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "bool":
        value = raw.lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"invalid bool: {raw!r}")
    raise ValueError(f"unsupported input type: {kind}")


def _resolve_inputs(contract: dict[str, Any], pairs: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    supplied: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ContractError(f"input must be KEY=VALUE: {pair!r}")
        key, value = pair.split("=", 1)
        if key in supplied:
            raise ContractError(f"duplicate supplied input: {key}")
        supplied[key] = value

    resolved: dict[str, Any] = {}
    recorded: dict[str, Any] = {}
    defs = {item["name"]: item for item in contract.get("inputs", [])}
    unknown = set(supplied) - set(defs)
    if unknown:
        raise ContractError(f"unknown input(s): {sorted(unknown)}")

    for name, definition in defs.items():
        if name in supplied:
            raw = supplied[name]
            try:
                value = _coerce(raw, definition["type"])
            except ValueError as exc:
                raise ContractError(f"input {name}: {exc}") from exc
        elif "default" in definition:
            value = definition["default"]
        elif definition.get("required", False):
            raise ContractError(f"missing required input: {name}")
        else:
            value = None

        resolved[name] = value
        recorded[name] = "<redacted>" if definition.get("sensitive", False) and value is not None else value

    return resolved, recorded


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _traffic_capture_start(run_dir: Path) -> tuple[subprocess.Popen[str], Any, Path, Path]:
    cdp_url = os.environ.get("BU_CDP_URL") or f"http://127.0.0.1:{os.environ.get('CDP_HOST_PORT', '9222')}"
    traffic_dir = run_dir / "traffic"
    traffic_dir.mkdir(parents=True, exist_ok=True)
    traffic_path = traffic_dir / "events.jsonl"
    state_path = traffic_dir / "state.json"
    ready_path = traffic_dir / ".ready"
    log_path = traffic_dir / "capture.log"
    log_handle = log_path.open("w", encoding="utf-8")

    process = subprocess.Popen(
        [
            sys.executable,
            str(AUTOMATIONS / "_runtime" / "traffic.py"),
            "--cdp-url",
            cdp_url,
            "--output",
            str(traffic_path),
            "--state-file",
            str(state_path),
            "--ready-file",
            str(ready_path),
        ],
        cwd=ROOT,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    for _ in range(50):
        if ready_path.exists():
            return process, log_handle, traffic_path, state_path
        if process.poll() is not None:
            break
        time.sleep(0.1)

    if process.poll() is None:
        process.terminate()
        process.wait(timeout=2)
    log_handle.close()

    detail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:] if log_path.exists() else ""
    raise ContractError(f"traffic capture failed to start against {cdp_url}: {detail.strip()}")


def _traffic_capture_stop(process: subprocess.Popen[str], log_handle: Any) -> bool:
    stopped_early = process.poll() is not None
    if not stopped_early:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    log_handle.close()
    return stopped_early


def _run(name: str, pairs: list[str], from_step: str | None, capture_traffic: bool) -> int:
    summary = validate(name)
    contract_path, contract = _load_contract(name)
    if contract.get("status") != "ready":
        raise ContractError(f"{name} is {contract.get('status')!r}; only ready automations can run")

    module_path = _module_path(contract_path, contract)
    inputs, recorded_inputs = _resolve_inputs(contract, pairs)
    steps = contract["algorithm"]
    if from_step is not None:
        target = next((step for step in steps if step["id"] == from_step), None)
        if target is None:
            raise ContractError(f"unknown checkpoint: {from_step}")
        if target["resume"] != "safe":
            raise ContractError(f"checkpoint {from_step!r} requires restart; rerun without --from")

    run_id = f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    run_dir = RUNS / name / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    manifest = {
        "schema": "uklok.browser.automation-run/v1",
        "layout": "uklok.browser.automation-run-layout/v2",
        "automation": name,
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "contract": str(contract_path.relative_to(ROOT)),
        "contract_sha256": _sha256(contract_path),
        "module": str(module_path.relative_to(ROOT)),
        "module_sha256": _sha256(module_path),
        "from_step": from_step,
        "steps": str((run_dir / "steps").relative_to(ROOT)),
        "capture_traffic": capture_traffic,
        "traffic": str((run_dir / "traffic" / "events.jsonl").relative_to(ROOT)) if capture_traffic else None,
        "traffic_state": str((run_dir / "traffic" / "state.json").relative_to(ROOT)) if capture_traffic else None,
        "traffic_checkpoints": str((run_dir / "traffic" / "checkpoints.jsonl").relative_to(ROOT)) if capture_traffic else None,
        "traffic_observability": {"requested": capture_traffic, "status": "pending" if capture_traffic else "disabled"},
        "inputs": recorded_inputs,
    }
    _json_dump(run_dir / "run.json", manifest)

    payload = {
        "root": str(ROOT),
        "automation": name,
        "contract_path": str(contract_path),
        "module_path": str(module_path),
        "run_dir": str(run_dir),
        "inputs": inputs,
        "recorded_inputs": recorded_inputs,
        "from_step": from_step,
        "capture_traffic": capture_traffic,
        "traffic": str((run_dir / "traffic" / "events.jsonl").relative_to(ROOT)) if capture_traffic else None,
        "traffic_state": str(run_dir / "traffic" / "state.json") if capture_traffic else None,
        "traffic_checkpoints": str(run_dir / "traffic" / "checkpoints.jsonl") if capture_traffic else None,
    }

    env = os.environ.copy()
    env["UKLOK_AUTOMATION_PAYLOAD"] = json.dumps(payload, default=str)
    runtime_dir = str((AUTOMATIONS / "_runtime").resolve())
    env["PYTHONPATH"] = runtime_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    source = "from execute import execute_from_env\nexecute_from_env(globals())\n"

    RUNS.mkdir(parents=True, exist_ok=True)
    lock_path = RUNS / "browser-lane.lock"
    with lock_path.open("a+") as lock:
        print(f"Waiting for shared browser lane: {name}")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        traffic_process = None
        traffic_log = None
        traffic_died_early = False
        try:
            if capture_traffic:
                try:
                    traffic_process, traffic_log, _, _ = _traffic_capture_start(run_dir)
                except ContractError as exc:
                    manifest["traffic_observability"] = {
                        "requested": True,
                        "status": "unavailable",
                        "error": str(exc),
                    }
                    _json_dump(run_dir / "run.json", manifest)
                    print(f"WARNING: traffic capture unavailable: {exc}", file=sys.stderr)
                else:
                    manifest["traffic_observability"] = {"requested": True, "status": "active"}
                    _json_dump(run_dir / "run.json", manifest)
                    print(f"Capturing raw Network.* CDP events: {run_dir.relative_to(ROOT)}/traffic/events.jsonl")

            proc = subprocess.run(
                [str(ROOT / "bin" / "browser-use")],
                input=source,
                text=True,
                cwd=ROOT,
                env=env,
                capture_output=True,
            )

            if traffic_process is not None:
                traffic_died_early = traffic_process.poll() is not None
        finally:
            if traffic_process is not None and traffic_log is not None:
                _traffic_capture_stop(traffic_process, traffic_log)

    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)

    if capture_traffic:
        if traffic_process is None:
            manifest["traffic_observability"].setdefault("status", "unavailable")
        elif traffic_died_early:
            manifest["traffic_observability"] = {
                "requested": True,
                "status": "degraded",
                "error": "traffic recorder stopped before automation completion",
            }
            print("WARNING: traffic recorder stopped before automation completion", file=sys.stderr)
        else:
            manifest["traffic_observability"] = {"requested": True, "status": "complete"}

    manifest["ended_at"] = _utc_now()
    manifest["status"] = "succeeded" if proc.returncode == 0 else "failed"
    _json_dump(run_dir / "run.json", manifest)

    failure_path = run_dir / "failure.json"
    if proc.returncode != 0 and not failure_path.exists():
        state = {}
        state_path = run_dir / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        _json_dump(
            failure_path,
            {
                "schema": "uklok.browser.automation-failure/v1",
                "automation": name,
                "run_id": run_id,
                "type": "browser_use_process",
                "checkpoint": state.get("current_step"),
                "step_dir": state.get("current_step_dir"),
                "contract": str(contract_path.relative_to(ROOT)),
                "module": str(module_path.relative_to(ROOT)),
                "stderr_tail": proc.stderr[-4000:],
                "traffic": manifest.get("traffic"),
                "traffic_checkpoints": manifest.get("traffic_checkpoints"),
                "traffic_observability": manifest.get("traffic_observability"),
                "agent_entrypoint": ".agents/skills/browser-automation/SKILL.md",
            },
        )

    if proc.returncode == 0:
        print(f"SUCCESS {name} — {run_dir.relative_to(ROOT)}/result.json")
    else:
        print(f"FAILED  {name} — {failure_path.relative_to(ROOT)}", file=sys.stderr)
    return proc.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="browser-automation", description="Contract-first deterministic browser automations")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="List tracked automations")

    validate_parser = commands.add_parser("validate", help="Validate contract/module alignment")
    validate_parser.add_argument("name", nargs="?")

    new_parser = commands.add_parser("new", help="Create an automation from the contract-first template")
    new_parser.add_argument("name")

    run_parser = commands.add_parser("run", help="Run an automation deterministically through Browser Use")
    run_parser.add_argument("name")
    run_parser.add_argument("--input", action="append", default=[], metavar="KEY=VALUE")
    run_parser.add_argument("--from", dest="from_step", metavar="CHECKPOINT")
    run_parser.add_argument(
        "--capture-traffic",
        action="store_true",
        help="Record raw Chrome Network.* CDP events to this run's traffic.jsonl",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "list":
            return list_automations()
        if args.command == "validate":
            return validate_many(args.name)
        if args.command == "new":
            return new_automation(args.name)
        if args.command == "run":
            return _run(args.name, args.input, args.from_step, args.capture_traffic)
    except ContractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
