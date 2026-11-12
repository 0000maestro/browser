#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import sys
import tempfile
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "automations" / "_runtime"
sys.path.insert(0, str(RUNTIME))

import cli  # noqa: E402
import operations  # noqa: E402

sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))
TRAFFIC_SPEC = importlib.util.spec_from_file_location("uklok_operation_traffic", RUNTIME / "traffic.py")
assert TRAFFIC_SPEC is not None and TRAFFIC_SPEC.loader is not None
traffic = importlib.util.module_from_spec(TRAFFIC_SPEC)
TRAFFIC_SPEC.loader.exec_module(traffic)


TARGET = {"targetId": "page-1", "type": "page"}
AUTH_SECRET = "BearerSecret-Alpha"
COOKIE_SECRET = "cookie-session-42"
SERVER_COOKIE_SECRET = "server-cookie-secret"
RESPONSE_SECRET = "response-token-secret"
GQL_VARIABLE_SECRET = "gql-var-secret"
GQL_LITERAL_SECRET = "literal-secret"
SIGNED_SECRET = "signed-query-secret"


def raw(seq: int, method: str, request_id: str, params: dict, *, session: str = "session-1") -> dict:
    value = dict(params)
    value["requestId"] = request_id
    return {
        "seq": seq,
        "captured_at": f"2026-11-08T00:00:{seq:02d}+00:00",
        "target": TARGET,
        "cdp": {"sessionId": session, "method": method, "params": value},
    }


def body(seq: int, request_id: str, value: str | None, *, status: str = "captured") -> dict:
    fact = {
        "kind": "response_body",
        "session_id": "session-1",
        "target_id": "page-1",
        "request_id": request_id,
        "status": status,
    }
    if value is not None:
        fact.update({"body": value, "base64_encoded": False, "byte_length": len(value.encode())})
    return {
        "seq": seq,
        "captured_at": f"2026-11-08T00:01:{seq:02d}+00:00",
        "target": TARGET,
        "capture": fact,
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value, sort_keys=True) + "\n" for value in values), encoding="utf-8")


def fixture(run: Path) -> None:
    for ordinal, step in enumerate(("step_a", "step_b", "step_c"), 1):
        write_json(run / "steps" / f"{ordinal:02d}-{step}" / "checkpoint.json", {"step": step})

    events = [
        raw(
            1,
            "Network.requestWillBeSent",
            "r1",
            {
                "type": "XHR",
                "request": {
                    "method": "GET",
                    "url": f"https://api.example.test/users/123456?api_key={AUTH_SECRET}&signature={SIGNED_SECRET}&assigned_to=alice",
                    "headers": {
                        "Authorization": f"Bearer {AUTH_SECRET}",
                        "Cookie": f"session={COOKIE_SECRET}",
                    },
                },
            },
        ),
        raw(
            2,
            "Network.requestWillBeSent",
            "r1",
            {
                "type": "XHR",
                "request": {
                    "method": "GET",
                    "url": f"https://api.example.test/users/654321?api_key={AUTH_SECRET}&signature={SIGNED_SECRET}&assigned_to=alice",
                    "headers": {
                        "Authorization": f"Bearer {AUTH_SECRET}",
                        "Cookie": f"session={COOKIE_SECRET}",
                    },
                },
                "redirectResponse": {"status": 302},
            },
        ),
        raw(
            3,
            "Network.responseReceived",
            "r1",
            {
                "type": "XHR",
                "response": {
                    "url": f"https://api.example.test/users/654321?api_key={AUTH_SECRET}&signature={SIGNED_SECRET}&assigned_to=alice",
                    "status": 200,
                    "mimeType": "application/json",
                    "headers": {"Set-Cookie": f"refresh={SERVER_COOKIE_SECRET}; Path=/; HttpOnly"},
                },
            },
        ),
        raw(
            4,
            "Network.requestWillBeSent",
            "asset",
            {
                "type": "Image",
                "request": {"method": "GET", "url": "https://api.example.test/logo.png", "headers": {}},
            },
        ),
        raw(
            5,
            "Network.requestWillBeSent",
            "r2",
            {
                "type": "XHR",
                "request": {
                    "method": "GET",
                    "url": f"https://api.example.test/users/987654?api_key={AUTH_SECRET}&signature={SIGNED_SECRET}&assigned_to=alice",
                    "headers": {
                        "Authorization": f"Bearer {AUTH_SECRET}",
                        "Cookie": f"session={COOKIE_SECRET}",
                    },
                },
            },
        ),
        body(6, "r1", json.dumps({"id": 654321, "name": "Ada", "token": RESPONSE_SECRET})),
        raw(
            7,
            "Network.responseReceived",
            "r2",
            {
                "type": "XHR",
                "response": {
                    "url": f"https://api.example.test/users/987654?api_key={AUTH_SECRET}&signature={SIGNED_SECRET}&assigned_to=alice",
                    "status": 200,
                    "mimeType": "application/json",
                    "headers": {},
                },
            },
        ),
        body(8, "r2", json.dumps({"id": 987654, "name": "Grace"})),
        raw(
            9,
            "Network.requestWillBeSent",
            "g1",
            {
                "type": "Fetch",
                "request": {
                    "method": "POST",
                    "url": "https://api.example.test/graphql",
                    "headers": {"Content-Type": "application/json"},
                    "postData": json.dumps(
                        {
                            "operationName": "GetThing",
                            "query": "query GetThing($token: String!) { thing(token: $token) { id name } }",
                            "variables": {"token": GQL_VARIABLE_SECRET},
                        }
                    ),
                },
            },
        ),
        raw(
            10,
            "Network.responseReceived",
            "g1",
            {
                "type": "Fetch",
                "response": {
                    "url": "https://api.example.test/graphql",
                    "status": 200,
                    "mimeType": "application/json",
                    "headers": {},
                },
            },
        ),
        body(11, "g1", json.dumps({"data": {"thing": {"id": "abc", "name": "One"}}})),
        raw(
            12,
            "Network.requestWillBeSent",
            "html",
            {
                "type": "Document",
                "request": {"method": "GET", "url": "https://api.example.test/home", "headers": {}},
            },
        ),
        raw(
            13,
            "Network.responseReceived",
            "html",
            {
                "type": "Document",
                "response": {
                    "url": "https://api.example.test/home",
                    "status": 200,
                    "mimeType": "text/html",
                    "headers": {"Content-Type": "text/html"},
                },
            },
        ),
        raw(
            14,
            "Network.requestWillBeSent",
            "g2",
            {
                "type": "Fetch",
                "request": {
                    "method": "POST",
                    "url": "https://api.example.test/graphql",
                    "headers": {"Content-Type": "application/json"},
                    "postData": json.dumps(
                        {
                            "operationName": "UpdateThing",
                            "query": f'mutation UpdateThing {{ updateThing(apiToken: "{GQL_LITERAL_SECRET}") {{ ok }} }}',
                            "variables": {},
                        }
                    ),
                },
            },
        ),
        raw(
            15,
            "Network.responseReceived",
            "g2",
            {
                "type": "Fetch",
                "response": {
                    "url": "https://api.example.test/graphql",
                    "status": 200,
                    "mimeType": "application/json",
                    "headers": {},
                },
            },
        ),
        body(16, "g2", json.dumps({"data": {"updateThing": {"ok": True}}})),
        raw(
            17,
            "Network.responseReceived",
            "orphan",
            {
                "type": "XHR",
                "response": {
                    "url": "https://api.example.test/orphan",
                    "status": 204,
                    "mimeType": "application/json",
                    "headers": {},
                },
            },
        ),
        raw(
            18,
            "Network.requestWillBeSent",
            "script",
            {
                "type": "Script",
                "request": {"method": "GET", "url": "https://api.example.test/app.js", "headers": {}},
            },
        ),
        raw(
            19,
            "Network.responseReceived",
            "script",
            {
                "type": "Script",
                "response": {
                    "url": "https://api.example.test/app.js",
                    "status": 200,
                    "mimeType": "application/javascript",
                    "headers": {},
                },
            },
        ),
        raw(
            20,
            "Network.requestWillBeSent",
            "nobody",
            {
                "type": "XHR",
                "request": {"method": "GET", "url": "https://api.example.test/nobody", "headers": {}},
            },
        ),
        raw(
            21,
            "Network.responseReceived",
            "nobody",
            {
                "type": "XHR",
                "response": {
                    "url": "https://api.example.test/nobody",
                    "status": 200,
                    "mimeType": "application/json",
                    "headers": {},
                },
            },
        ),
        {
            **raw(
                22,
                "Network.requestWillBeSent",
                "human",
                {
                    "type": "XHR",
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.test/human",
                        "headers": {},
                    },
                },
            ),
            "target": {"targetId": "human-page", "type": "page"},
        },
    ]
    write_jsonl(run / "traffic" / "events.jsonl", events)
    write_jsonl(
        run / "traffic" / "checkpoints.jsonl",
        [
            {"step": "step_a", "from_seq": 1, "through_seq": 4, "target_id": "page-1"},
            {"step": "step_b", "from_seq": 5, "through_seq": 13, "target_id": "page-1"},
            {"step": "step_c", "from_seq": 14, "through_seq": 22, "target_id": "page-1"},
        ],
    )


def derived_bytes(run: Path) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    roots = [run / "operations"]
    roots.extend(path / "operations" for path in sorted((run / "steps").glob("*")))
    for root in roots:
        if root.is_dir():
            for path in sorted(root.rglob("*.json")):
                values[str(path.relative_to(run))] = path.read_bytes()
    values["traffic/secrets.env"] = (run / "traffic" / "secrets.env").read_bytes()
    return values


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def prove_recorder_body_closure() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        recorder = traffic.CDPTrafficRecorder(None, tmp / "events.jsonl", tmp / "state.json")
        recorder.session_to_target["session-1"] = TARGET

        async def exercise() -> None:
            async def fake_send(method: str, params: dict | None = None, session_id: str | None = None) -> dict:
                assert method == "Network.getResponseBody"
                assert params == {"requestId": "body-request"}
                assert session_id == "session-1"
                await asyncio.sleep(0)
                return {"body": '{"ok":true}', "base64Encoded": False}

            recorder.send = fake_send
            recorder._observe_network_state(
                {
                    "sessionId": "session-1",
                    "method": "Network.requestWillBeSent",
                    "params": {
                        "requestId": "body-request",
                        "type": "Fetch",
                        "request": {
                            "method": "GET",
                            "url": "https://api.example.test/body",
                        },
                    },
                }
            )
            recorder._observe_network_state(
                {
                    "sessionId": "session-1",
                    "method": "Network.responseReceived",
                    "params": {
                        "requestId": "body-request",
                        "type": "Fetch",
                        "response": {"status": 200, "mimeType": "application/json"},
                    },
                }
            )
            recorder._observe_network_state(
                {
                    "sessionId": "session-1",
                    "method": "Network.loadingFinished",
                    "params": {"requestId": "body-request", "encodedDataLength": 11},
                }
            )
            key = "session-1:body-request"
            assert recorder.active_requests[key]["phase"] == "body_pending"
            await asyncio.gather(*list(recorder.tasks))
            assert key not in recorder.active_requests

            oversized_key = "session-1:oversized"
            recorder.active_requests[oversized_key] = {
                "key": oversized_key,
                "session_id": "session-1",
                "target_id": "page-1",
                "request_id": "oversized",
                "url": "https://api.example.test/oversized",
                "method": "GET",
                "type": "XHR",
                "started_at": 0,
                "response_status": 200,
                "mime_type": "application/json",
            }

            async def must_not_send(*args, **kwargs):
                raise AssertionError("oversized body must be rejected before getResponseBody")

            recorder.send = must_not_send
            await recorder.capture_response_body(
                key=oversized_key,
                session_id="session-1",
                request_id="oversized",
                encoded_data_length=traffic.BODY_MAX_BYTES + 1,
            )

        try:
            asyncio.run(exercise())
        finally:
            recorder.handle.close()

        facts = [
            json.loads(line)
            for line in (tmp / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert facts[0]["capture"]["kind"] == "response_body"
        assert facts[0]["capture"]["status"] == "captured"
        assert "cdp" not in facts[0]
        assert facts[1]["capture"]["status"] == "oversized"
        assert "body" not in facts[1]["capture"]


prove_recorder_body_closure()


runs_root = ROOT / ".runs"
runs_root.mkdir(exist_ok=True)
run = Path(tempfile.mkdtemp(prefix="operation-reading-fixture-", dir=runs_root))
try:
    fixture(run)

    # 1. A request opened in A keeps A ownership even though its body fact lands in B.
    index = operations.derive_operations(run)
    step_a = run / "steps" / "01-step_a" / "operations"
    endpoints_a = read(step_a / "endpoints.json")["operations"]
    user_a = next(item for item in endpoints_a if item["path"] == "/users/{id}")
    assert user_a["first_step"] == "step_a"
    assert user_a["provenance"][0]["opening_seq"] == 1
    assert user_a["response"]["body_evidence"][0]["status"] == "captured"
    openapi_a = read(step_a / "openapi" / "api.example.test.json")
    user_schema = openapi_a["paths"]["/users/{id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert "token" in user_schema["properties"]
    assert "required" not in user_schema

    # 2. Redirects remain one call, and final URL participates in templated grouping.
    assert user_a["provenance"][0]["redirects"] == 1
    assert user_a["path"] == "/users/{id}"

    # 3. Repeated high-cardinality identifiers de-duplicate to one stable operation.
    endpoints_b = read(run / "steps" / "02-step_b" / "operations" / "endpoints.json")["operations"]
    user_b = next(item for item in endpoints_b if item["path"] == "/users/{id}")
    assert user_a["key"] == user_b["key"]
    assert user_a["shared_across_steps"] is True
    assert index["steps"]["step_a"]["shared_operations"] == [user_a["key"]]

    # 4. GraphQL operations sharing one HTTP endpoint remain semantically distinct.
    gql_b = read(run / "steps" / "02-step_b" / "operations" / "graphql" / "operations.json")["operations"]
    gql_c = read(run / "steps" / "03-step_c" / "operations" / "graphql" / "operations.json")["operations"]
    assert [item["operation_name"] for item in gql_b] == ["GetThing"]
    assert [item["operation_name"] for item in gql_c] == ["UpdateThing"]
    assert gql_b[0]["key"] != gql_c[0]["key"]
    observed_model = read(run / "steps" / "02-step_b" / "operations" / "graphql" / "observed-schema.json")
    assert observed_model["complete"] is False and observed_model["authority"] == "observed-only"
    anon_a = {"post_data": json.dumps({"query": '{ thing(id: "123") { id } }'})}
    anon_b = {"post_data": json.dumps({"query": '{ thing(id: "999") { id } }'})}
    assert operations._gql(anon_a)[0] == operations._gql(anon_b)[0]
    assert operations._gql(anon_a)[0].startswith("anonymous-")

    # 5. Schemas come only from observed bodies; missing body evidence stays unobserved.
    openapi_c = read(run / "steps" / "03-step_c" / "operations" / "openapi" / "api.example.test.json")
    nobody = openapi_c["paths"]["/nobody"]["get"]["responses"]["200"]
    assert nobody["x-uklok-body-evidence"] == [{"status": "unobserved"}]
    assert "content" not in nobody
    user_query = next(
        item for item in openapi_a["paths"]["/users/{id}"]["get"]["parameters"]
        if item["in"] == "query" and item["name"] == "api_key"
    )
    assert user_query["required"] is False
    assert user_query["x-uklok-observed-on-all-calls"] is True

    # 6. Reused secrets are lifted once and referenced by placeholders everywhere.
    secrets_text = (run / "traffic" / "secrets.env").read_text(encoding="utf-8")
    assert secrets_text.count(AUTH_SECRET) == 1
    assert "Path=/" not in secrets_text and '="/"' not in secrets_text
    auth_a = read(step_a / "auth.json")["dependencies"]
    auth_header = next(item for item in auth_a if item["kind"] == "header" and item["name"] == "Authorization")
    auth_query = next(item for item in auth_a if item["kind"] == "query" and item["name"] == "api_key")
    signed_query = next(item for item in auth_a if item["kind"] == "query" and item["name"] == "signature")
    assert auth_header["placeholders"] == auth_query["placeholders"]
    assert signed_query["placeholders"]
    assert any(
        rule["scope"] == "operation"
        and rule["context"] == user_a["key"]
        and rule["kind"] == "header"
        and rule["name"] == "Authorization"
        for rule in index["credential_rules"]
    )
    assert all(item["name"] != "assigned_to" for item in auth_a)
    assert "alice" not in secrets_text
    for secret in (
        AUTH_SECRET,
        COOKIE_SECRET,
        SERVER_COOKIE_SECRET,
        RESPONSE_SECRET,
        GQL_VARIABLE_SECRET,
        GQL_LITERAL_SECRET,
        SIGNED_SECRET,
    ):
        assert secret in secrets_text
        for path, value in derived_bytes(run).items():
            if path != "traffic/secrets.env":
                assert secret.encode() not in value, (secret, path)
    assert "$" + "{UKLOK_SECRET_" in gql_c[0]["documents"][0]

    # 7. Integrity verification fails closed when a lifted value is poisoned back into derived output.
    poisoned_path = run / "operations" / "index.json"
    poisoned = read(poisoned_path)
    poisoned["poison"] = AUTH_SECRET
    write_json(poisoned_path, poisoned)
    try:
        operations.verify_reading(run)
    except operations.OperationReadError:
        pass
    else:
        raise AssertionError("poisoned derived output must fail secret integrity")
    operations.derive_operations(run)

    # 8. HTML documents and static assets are not API operations.
    keys = "\n".join(index["operation_keys"])
    assert "/home" not in keys and "logo.png" not in keys and "app.js" not in keys

    # 9. A response without its opening event remains explicitly unattributed.
    index = read(run / "operations" / "index.json")
    assert index["unattributed_count"] == 2
    assert {item["request_id"] for item in index["unattributed"]} == {"orphan", "human"}

    # 10. Assembled calls and grouped counts reconcile exactly.
    assert index["total_observed_calls"] == 7
    assert index["integrity"] == {
        "assembled_api_calls": 7,
        "grouped_call_count": 7,
        "reconciles": True,
    }
    assert sum(index["operation_counts"].values()) == 7

    # 11. Re-reading deterministically replaces the same derived surface.
    first = derived_bytes(run)
    (step_a / "stale.json").write_text('{"stale":true}\n', encoding="utf-8")
    operations.derive_operations(run)
    second = derived_bytes(run)
    assert first == second
    assert not (step_a / "stale.json").exists()

    # 12. Both the writer and CLI run selector refuse path escape.
    try:
        operations._safe(run, "..", "escaped.json")
    except operations.OperationReadError:
        pass
    else:
        raise AssertionError("derived writes must not escape the run directory")
    try:
        cli._operation_run_dir(ROOT)
    except cli.ContractError:
        pass
    else:
        raise AssertionError("operations command must stay under .runs/")

    # 13. Explicit reading and capture+reading use the same deterministic derivation owner.
    explicit = derived_bytes(run)
    shutil.rmtree(run / "operations")
    for step_dir in (run / "steps").glob("*"):
        shutil.rmtree(step_dir / "operations", ignore_errors=True)
    composite_index = cli._derive_run_operations(run)
    composite = derived_bytes(run)
    assert explicit == composite
    assert composite_index["operation_keys"] == index["operation_keys"]
    args = cli.build_parser().parse_args(
        ["run", "example_read_page", "--capture-traffic", "--derive-operations"]
    )
    assert args.capture_traffic is True and args.derive_operations is True
    try:
        cli._run("example_read_page", [], None, False, False, True)
    except cli.ContractError as exc:
        assert "--derive-operations requires --capture-traffic" in str(exc)
    else:
        raise AssertionError("capture+reading must require capture")

    print("operation reading fixture validation passed")
finally:
    shutil.rmtree(run, ignore_errors=True)
