from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

API_TYPES = {"XHR", "Fetch", "EventSource"}
ASSET_TYPES = {"Stylesheet", "Image", "Media", "Font", "Script"}
JSON_MARKERS = ("application/json", "+json", "application/graphql-response+json")
SENSITIVE = re.compile(r"(authorization|auth|api.?key|token|secret|password|passwd|session|cookie|credential|csrf|xsrf)", re.I)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27,}$", re.I)
MIXED_ID = re.compile(r"^[A-Za-z0-9_-]{12,}$")
GQL_NAME = re.compile(r"\b(?:query|mutation|subscription)\s+([_A-Za-z][_0-9A-Za-z]*)")
GQL_SECRET_ARG = re.compile(r"""([_A-Za-z][_0-9A-Za-z]*(?:token|secret|password|passwd|api_?key|session|credential|csrf|xsrf)[_0-9A-Za-z]*)\s*:\s*("(?:\\.|[^"])*"|'(?:\\.|[^'])*'|[-+]?\d+(?:\.\d+)?|true|false|null)""", re.I)


class OperationReadError(RuntimeError):
    pass


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise OperationReadError(f"missing required traffic evidence: {path}")
    out = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OperationReadError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if isinstance(value, dict):
            out.append(value)
    return out


def _key(session: Any, request: Any) -> str:
    return f"{session or 'browser'}:{request or 'unknown'}"


def _headers(value: Any) -> dict[str, Any]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def _merge_headers(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = dict(left)
    lower = {k.lower(): k for k in result}
    for key, value in right.items():
        previous = lower.get(key.lower())
        if previous and previous != key:
            result.pop(previous, None)
        result[key] = value
        lower[key.lower()] = key
    return result


def _header(headers: dict[str, Any], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return ""


def _new_call(key: str, session: Any, request: Any) -> dict[str, Any]:
    return {
        "key": key,
        "session_id": str(session) if session is not None else None,
        "request_id": str(request) if request is not None else None,
        "target_id": None,
        "opening_target_id": None,
        "opening_seq": None,
        "last_seq": None,
        "method": None,
        "url": None,
        "type": None,
        "request_headers": {},
        "response_headers": {},
        "post_data": None,
        "status": None,
        "mime": None,
        "redirects": 0,
        "body": None,
        "owner": None,
    }


def _assemble(run: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    calls: dict[str, dict[str, Any]] = {}

    def ensure(session: Any, request: Any) -> dict[str, Any]:
        key = _key(session, request)
        calls.setdefault(key, _new_call(key, session, request))
        return calls[key]

    for envelope in _jsonl(run / "traffic" / "events.jsonl"):
        try:
            seq = int(envelope.get("seq"))
        except (TypeError, ValueError):
            continue
        target = envelope.get("target") or {}
        target_id = str(target.get("targetId")) if isinstance(target, dict) and target.get("targetId") else None
        raw = envelope.get("cdp")
        if isinstance(raw, dict):
            params = raw.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            request_id = params.get("requestId")
            if request_id is not None:
                call = ensure(raw.get("sessionId"), request_id)
                call["last_seq"] = seq
                call["target_id"] = call["target_id"] or target_id
                method = raw.get("method")
                if method == "Network.requestWillBeSent":
                    request = params.get("request") or {}
                    if call["opening_seq"] is None:
                        call["opening_seq"] = seq
                        call["opening_target_id"] = target_id
                    else:
                        call["redirects"] += 1
                    call["method"] = str(request.get("method") or call["method"] or "UNKNOWN")
                    call["url"] = str(request.get("url") or call["url"] or "")
                    call["type"] = str(params.get("type") or call["type"] or "Other")
                    call["request_headers"] = _merge_headers(call["request_headers"], _headers(request.get("headers")))
                    if isinstance(request.get("postData"), str):
                        call["post_data"] = request["postData"]
                elif method == "Network.requestWillBeSentExtraInfo":
                    call["request_headers"] = _merge_headers(call["request_headers"], _headers(params.get("headers")))
                elif method == "Network.responseReceived":
                    response = params.get("response") or {}
                    call["url"] = call["url"] or response.get("url")
                    call["type"] = str(params.get("type") or call["type"] or "Other")
                    call["status"] = response.get("status")
                    call["mime"] = str(response.get("mimeType") or call["mime"] or "")
                    call["response_headers"] = _merge_headers(call["response_headers"], _headers(response.get("headers")))
                elif method == "Network.responseReceivedExtraInfo":
                    call["response_headers"] = _merge_headers(call["response_headers"], _headers(params.get("headers")))
                    call["status"] = call["status"] if call["status"] is not None else params.get("statusCode")
                elif method == "Network.loadingFailed" and call["body"] is None:
                    call["body"] = {"status": "unavailable", "reason": "loading_failed"}

        fact = envelope.get("capture")
        if isinstance(fact, dict) and fact.get("kind") == "response_body":
            call = ensure(fact.get("session_id"), fact.get("request_id"))
            call["last_seq"] = seq
            call["target_id"] = call["target_id"] or fact.get("target_id") or target_id
            call["url"] = call["url"] or fact.get("url")
            call["method"] = call["method"] or fact.get("method")
            call["type"] = call["type"] or fact.get("type")
            call["body"] = dict(fact)

    boundaries = []
    for raw in _jsonl(run / "traffic" / "checkpoints.jsonl"):
        try:
            boundaries.append(
                {
                    "step": str(raw.get("step")),
                    "from_seq": int(raw.get("from_seq")),
                    "through_seq": int(raw.get("through_seq")),
                    "target_id": str(raw.get("target_id")) if raw.get("target_id") else None,
                }
            )
        except (TypeError, ValueError):
            continue
    boundaries.sort(key=lambda x: (x["from_seq"], x["through_seq"]))

    for call in calls.values():
        opening = call["opening_seq"]
        if opening is None:
            continue
        for boundary in boundaries:
            if boundary["from_seq"] <= opening <= boundary["through_seq"]:
                if boundary["target_id"] and call["opening_target_id"] and boundary["target_id"] != call["opening_target_id"]:
                    continue
                call["owner"] = boundary["step"]
                break
    return list(calls.values()), boundaries


def _identifier(segment: str) -> bool:
    if UUID.fullmatch(segment) or (segment.isdigit() and len(segment) >= 4):
        return True
    if re.fullmatch(r"[0-9a-f]{12,}", segment, re.I):
        return True
    return bool(MIXED_ID.fullmatch(segment) and any(c.isalpha() for c in segment) and any(c.isdigit() for c in segment))


def _template(path: str) -> tuple[str, list[str]]:
    parts = path.split("/")
    params, n = [], 0
    for i, part in enumerate(parts):
        if part and _identifier(part):
            n += 1
            name = "id" if n == 1 else f"id{n}"
            parts[i] = "{" + name + "}"
            params.append(name)
    value = "/".join(parts) or "/"
    return (value if value.startswith("/") else "/" + value), params


def _parse_json(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _body_text(call: dict[str, Any]) -> str | None:
    fact = call.get("body") or {}
    if fact.get("status") != "captured" or not isinstance(fact.get("body"), str):
        return None
    if fact.get("base64_encoded"):
        try:
            return base64.b64decode(fact["body"]).decode("utf-8")
        except Exception:
            return None
    return fact["body"]


def _gql(call: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    payload = _parse_json(call.get("post_data"))
    if not isinstance(payload, dict):
        return None, None
    query = payload.get("query")
    name = payload.get("operationName")
    if not (isinstance(query, str) or isinstance(name, str)):
        return None, None
    if isinstance(name, str) and name.strip():
        return name.strip(), payload
    if isinstance(query, str):
        match = GQL_NAME.search(query)
        if match:
            return match.group(1), payload
        if "{" in query:
            normalized = " ".join(query.split())
            return "anonymous-" + hashlib.sha256(normalized.encode()).hexdigest()[:12], payload
    return None, None


def _api(call: dict[str, Any]) -> bool:
    resource = call.get("type") or "Other"
    request_ct = _header(call["request_headers"], "content-type").lower()
    response_ct = (_header(call["response_headers"], "content-type") + ";" + str(call.get("mime") or "")).lower()
    if resource in ASSET_TYPES:
        return False
    if resource == "Document" and "text/html" in response_ct:
        return False
    if _gql(call)[0]:
        return True
    return (
        resource in API_TYPES
        or any(marker in request_ct or marker in response_ct for marker in JSON_MARKERS)
        or "text/event-stream" in response_ct
    )


def _identity(call: dict[str, Any]) -> dict[str, Any]:
    url = urlsplit(str(call.get("url") or ""))
    path, path_params = _template(url.path or "/")
    method = str(call.get("method") or "UNKNOWN").upper()
    gql_name, payload = _gql(call)
    host = url.netloc or "unknown-host"
    if gql_name:
        return {
            "key": f"graphql|{host}|{path}|{gql_name}",
            "kind": "graphql",
            "host": host,
            "scheme": url.scheme or "https",
            "path": path,
            "path_params": path_params,
            "method": method,
            "gql_name": gql_name,
            "payload": payload,
        }
    return {
        "key": f"http|{method}|{host}|{path}",
        "kind": "http",
        "host": host,
        "scheme": url.scheme or "https",
        "path": path,
        "path_params": path_params,
        "method": method,
    }


class Secrets:
    def __init__(self) -> None:
        self.values: list[str] = []
        self.refs: dict[str, str] = {}

    def add(self, value: Any) -> str | None:
        if value is None or isinstance(value, (dict, list)):
            return None
        text = str(value)
        if not text:
            return None
        if text not in self.refs:
            self.values.append(text)
            self.refs[text] = "$" + f"{{UKLOK_SECRET_{len(self.values):03d}}}"
        return self.refs[text]

    def redact(self, text: str) -> str:
        for secret in sorted(self.values, key=len, reverse=True):
            text = text.replace(secret, self.refs[secret])
        return text


def _sensitive(name: str) -> bool:
    return bool(SENSITIVE.search(name))


def _cookies(value: str) -> list[tuple[str, str]]:
    out = []
    for part in value.split(";"):
        if "=" in part:
            name, val = part.split("=", 1)
            if name.strip():
                out.append((name.strip(), val.strip()))
    return out


def _set_cookies(value: str) -> list[tuple[str, str]]:
    out = []
    for line in value.splitlines() or [value]:
        first = line.split(";", 1)[0]
        if "=" in first:
            name, val = first.split("=", 1)
            if name.strip():
                out.append((name.strip(), val.strip()))
    return out


def _walk_sensitive(value: Any, secrets: Secrets, deps: list[dict[str, str]], prefix: str = "", response: bool = False) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if _sensitive(str(key)) and not isinstance(child, (dict, list)):
                ref = secrets.add(child)
                if ref and not response:
                    deps.append({"kind": "body", "name": path, "placeholder": ref})
            _walk_sensitive(child, secrets, deps, path, response)
    elif isinstance(value, list):
        for child in value:
            _walk_sensitive(child, secrets, deps, prefix, response)


def _collect(calls: list[dict[str, Any]]) -> tuple[Secrets, dict[str, list[dict[str, str]]]]:
    secrets, by_call = Secrets(), {}
    for call in calls:
        deps = []
        for name, value in call["request_headers"].items():
            lower, text = name.lower(), str(value)
            if lower == "authorization":
                parts = text.split(None, 1)
                ref = secrets.add(parts[1] if len(parts) > 1 else text)
                if ref:
                    deps.append({"kind": "header", "name": name, "placeholder": ref})
            elif lower == "cookie":
                for cookie_name, cookie_value in _cookies(text):
                    ref = secrets.add(cookie_value)
                    if ref:
                        deps.append({"kind": "cookie", "name": cookie_name, "placeholder": ref})
            elif _sensitive(name):
                ref = secrets.add(text)
                if ref:
                    deps.append({"kind": "header", "name": name, "placeholder": ref})
        if call.get("url"):
            for name, value in parse_qsl(urlsplit(call["url"]).query, keep_blank_values=True):
                if _sensitive(name):
                    ref = secrets.add(value)
                    if ref:
                        deps.append({"kind": "query", "name": name, "placeholder": ref})
        body = _parse_json(call.get("post_data"))
        if body is not None:
            _walk_sensitive(body, secrets, deps)
        _, gql_payload = _gql(call)
        if gql_payload and isinstance(gql_payload.get("query"), str):
            for match in GQL_SECRET_ARG.finditer(gql_payload["query"]):
                raw = match.group(2).strip()
                value = raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"} else raw
                ref = secrets.add(value)
                if ref:
                    deps.append({"kind": "graphql-argument", "name": match.group(1), "placeholder": ref})
        response = _parse_json(_body_text(call))
        if response is not None:
            _walk_sensitive(response, secrets, [], response=True)
        for name, value in call["response_headers"].items():
            if name.lower() == "set-cookie":
                for _, cookie_value in _set_cookies(str(value)):
                    secrets.add(cookie_value)
            elif _sensitive(name):
                secrets.add(value)
        unique = {(d["kind"], d["name"].lower(), d["placeholder"]): d for d in deps}
        by_call[call["key"]] = [unique[k] for k in sorted(unique)]
    return secrets, by_call


def _schema(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array", "items": _merge_schema([_schema(v) for v in value]) if value else {}}
    if isinstance(value, dict):
        props = {str(k): _schema(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
        return {"type": "object", "properties": props, "required": sorted(props), "additionalProperties": True}
    return {}


def _merge_schema(values: list[dict[str, Any]]) -> dict[str, Any]:
    values = [v for v in values if v]
    if not values:
        return {}
    if len({json.dumps(v, sort_keys=True) for v in values}) == 1:
        return values[0]
    types = sorted({str(v.get("type")) for v in values if v.get("type")})
    if types == ["object"]:
        keys = sorted({k for v in values for k in (v.get("properties") or {})})
        props = {k: _merge_schema([v["properties"][k] for v in values if k in (v.get("properties") or {})]) for k in keys}
        required = sorted(set.intersection(*[set(v.get("required") or []) for v in values]))
        out = {"type": "object", "properties": props, "additionalProperties": True}
        if required:
            out["required"] = required
        return out
    if types == ["array"]:
        return {"type": "array", "items": _merge_schema([v.get("items") or {} for v in values])}
    return {"type": types[0] if len(types) == 1 else types}


def _group(calls: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for call in calls:
        out.setdefault(_identity(call)["key"], []).append(call)
    return out


def _provenance(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_key": call["key"],
        "request_id": call["request_id"],
        "opening_seq": call["opening_seq"],
        "last_observed_seq": call["last_seq"],
        "redirects": call["redirects"],
    }


def _body_evidence(call: dict[str, Any]) -> dict[str, Any]:
    fact = call.get("body") or {}
    out = {"status": str(fact.get("status") or "unobserved")}
    for key in ("reason", "byte_length", "encoded_data_length"):
        if fact.get(key) is not None:
            out[key] = fact[key]
    return out


def _dependency_names(calls: list[dict[str, Any]], deps: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for call in calls:
        for dependency in deps.get(call["key"], []):
            grouped.setdefault((dependency["kind"], dependency["name"]), set()).add(dependency["placeholder"])
    return [
        {"kind": kind, "name": name, "placeholders": sorted(grouped[(kind, name)])}
        for kind, name in sorted(grouped)
    ]


def _endpoint_records(calls: list[dict[str, Any]], deps: dict[str, list[dict[str, str]]], steps_by_key: dict[str, set[str]]) -> list[dict[str, Any]]:
    out = []
    for key, items in sorted(_group(calls).items()):
        ident = _identity(items[0])
        header_sets = [{h.lower(): h for h in c["request_headers"]} for c in items]
        all_headers = sorted(set.intersection(*[set(x) for x in header_sets])) if header_sets else []
        any_headers = sorted(set().union(*[set(x) for x in header_sets])) if header_sets else []
        query: dict[str, set[str]] = {}
        for call in items:
            for name, value in parse_qsl(urlsplit(str(call.get("url") or "")).query, keep_blank_values=True):
                kind = "boolean" if value.lower() in {"true", "false"} else "integer" if re.fullmatch(r"[-+]?\d+", value) else "number" if re.fullmatch(r"[-+]?\d+\.\d+", value) else "string"
                query.setdefault(name, set()).add(kind)
        out.append(
            {
                "key": key,
                "kind": ident["kind"],
                "method": ident["method"],
                "host": ident["host"],
                "path": ident["path"],
                "count": len(items),
                "steps": sorted({c["owner"] for c in items if c["owner"]}),
                "first_step": next(
                    (c["owner"] for c in sorted(items, key=lambda value: value["opening_seq"] if value["opening_seq"] is not None else 10**18) if c["owner"]),
                    None,
                ),
                "shared_across_steps": len(steps_by_key.get(key, set())) > 1,
                "request": {
                    "path_parameters": ident["path_params"],
                    "query_parameters": {k: sorted(v) for k, v in sorted(query.items())},
                    "headers": {"observed_on_all_calls": all_headers, "observed_on_any_call": any_headers},
                },
                "response": {
                    "statuses": sorted({str(c["status"]) for c in items if c["status"] is not None}),
                    "body_evidence": [_body_evidence(c) for c in items],
                    "sets_cookies": sorted({n for c in items for h, v in c["response_headers"].items() if h.lower() == "set-cookie" for n, _ in _set_cookies(str(v))}),
                },
                "credential_dependencies": _dependency_names(items, deps),
                "provenance": [_provenance(c) for c in items],
            }
        )
    return out


def _graphql(calls: list[dict[str, Any]], secrets: Secrets) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, models = [], []
    for key, items in sorted(_group(calls).items()):
        ident = _identity(items[0])
        if ident["kind"] != "graphql":
            continue
        docs, var_schemas, result_schemas = set(), [], []
        for call in items:
            _, payload = _gql(call)
            if payload and isinstance(payload.get("query"), str):
                docs.add(secrets.redact(payload["query"]))
            if payload and payload.get("variables") is not None:
                var_schemas.append(_schema(payload["variables"]))
            result = _parse_json(_body_text(call))
            if result is not None:
                result_schemas.append(_schema(result))
        record = {
            "key": key,
            "operation_name": ident["gql_name"],
            "host": ident["host"],
            "path": ident["path"],
            "method": ident["method"],
            "count": len(items),
            "documents": sorted(docs),
            "observed_variables_schema": _merge_schema(var_schemas),
            "observed_result_schema": _merge_schema(result_schemas),
            "provenance": [_provenance(c) for c in items],
        }
        records.append(record)
        models.append({"operation_name": ident["gql_name"], "operation_key": key, "variables": record["observed_variables_schema"], "result": record["observed_result_schema"]})
    return records, {
        "schema": "uklok.browser.graphql-observed-schema/v1",
        "complete": False,
        "authority": "observed-only",
        "note": "Observed captured operations only; not the server's authoritative GraphQL schema.",
        "operations": models,
    }


def _openapi(host: str, calls: list[dict[str, Any]], deps: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    scheme = "https"
    for call in calls:
        ident = _identity(call)
        if ident["host"] == host and ident["kind"] == "http":
            scheme = ident["scheme"]
            buckets.setdefault((ident["path"], ident["method"].lower()), []).append(call)
    paths = {}
    for (path, method), items in sorted(buckets.items()):
        ident = _identity(items[0])
        params = [{"name": n, "in": "path", "required": True, "schema": {"type": "string"}} for n in ident["path_params"]]
        query_names = sorted({n for c in items for n, _ in parse_qsl(urlsplit(str(c.get("url") or "")).query, keep_blank_values=True)})
        params.extend({"name": n, "in": "query", "required": all(n in {q for q, _ in parse_qsl(urlsplit(str(c.get("url") or "")).query, keep_blank_values=True)} for c in items), "schema": {"type": "string"}} for n in query_names)
        request_schemas = [_schema(v) for c in items for v in [_parse_json(c.get("post_data"))] if v is not None]
        statuses = sorted({str(c["status"]) for c in items if c["status"] is not None}) or ["default"]
        responses = {}
        for status in statuses:
            matched = items if status == "default" else [c for c in items if str(c["status"]) == status]
            schemas = [_schema(v) for c in matched for v in [_parse_json(_body_text(c))] if v is not None]
            response = {"description": "Observed response", "x-uklok-body-evidence": [_body_evidence(c) for c in matched]}
            if schemas:
                response["content"] = {"application/json": {"schema": _merge_schema(schemas)}}
            responses[status] = response
        op = {
            "summary": "Observed operation",
            "parameters": params,
            "responses": responses,
            "x-uklok-observed-count": len(items),
            "x-uklok-credential-dependencies": _dependency_names(items, deps),
            "x-uklok-provenance": [_provenance(c) for c in items],
        }
        if request_schemas:
            op["requestBody"] = {"content": {"application/json": {"schema": _merge_schema(request_schemas)}}}
        gql_names = sorted({_identity(c).get("gql_name") for c in items if _identity(c)["kind"] == "graphql"})
        if gql_names:
            op["x-uklok-graphql-operations"] = gql_names
        paths.setdefault(path, {})[method] = op
    return {
        "openapi": "3.1.0",
        "info": {"title": f"Observed API surface for {host}", "version": "observed", "description": "Observed browser traffic only; not a completeness claim."},
        "servers": [{"url": f"{scheme}://{host}"}],
        "paths": paths,
        "x-uklok-observed-only": True,
    }


def _safe(root: Path, *parts: str) -> Path:
    root = root.resolve()
    target = root.joinpath(*parts).resolve()
    if target != root and root not in target.parents:
        raise OperationReadError(f"derived path escaped run directory: {target}")
    return target


def _write(run: Path, relative: str, value: Any) -> None:
    path = _safe(run, *Path(relative).parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _step_dirs(run: Path) -> dict[str, Path]:
    out = {}
    for directory in sorted((run / "steps").glob("*")) if (run / "steps").is_dir() else []:
        checkpoint = directory / "checkpoint.json"
        try:
            value = json.loads(checkpoint.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value.get("step"), str):
            out[value["step"]] = directory
    return out


def _secret_store(run: Path, secrets: Secrets) -> None:
    path = _safe(run, "traffic", "secrets.env")
    path.write_text("\n".join(f"UKLOK_SECRET_{i:03d}={json.dumps(v, ensure_ascii=False)}" for i, v in enumerate(secrets.values, 1)) + ("\n" if secrets.values else ""), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _secret_values(run: Path) -> list[str]:
    path = run / "traffic" / "secrets.env"
    values = []
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line:
                try:
                    value = json.loads(line.split("=", 1)[1])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, str):
                    values.append(value)
    return values


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [str(k) for k in value] + [s for v in value.values() for s in _strings(v)]
    return []


def verify_reading(run: Path) -> None:
    secrets = _secret_values(run)
    if not secrets:
        return
    files = list((run / "operations").rglob("*.json")) if (run / "operations").is_dir() else []
    files += [p for d in (run / "steps").glob("*/operations") for p in d.rglob("*.json")] if (run / "steps").is_dir() else []
    for path in files:
        values = _strings(json.loads(path.read_text()))
        for secret in secrets:
            if secret in values or (len(secret) >= 4 and any(secret in value for value in values)):
                raise OperationReadError(f"secret survived in derived artifact: {path}")


def derive_operations(run: Path, step: str | None = None) -> dict[str, Any]:
    run = run.resolve()
    if not run.is_dir():
        raise OperationReadError(f"run directory does not exist: {run}")
    assembled, boundaries = _assemble(run)
    calls = [call for call in assembled if _api(call)]
    secrets, deps = _collect(calls)
    grouped = _group(calls)
    steps_by_key = {key: {c["owner"] for c in items if c["owner"]} for key, items in grouped.items()}
    known_steps = {b["step"] for b in boundaries}
    if step is not None and step not in known_steps:
        raise OperationReadError(f"unknown captured step: {step}")
    dirs = _step_dirs(run)
    selected = [step] if step is not None else sorted(known_steps)

    if step is None:
        for directory in dirs.values():
            if (directory / "operations").exists():
                shutil.rmtree(directory / "operations")
    elif step in dirs and (dirs[step] / "operations").exists():
        shutil.rmtree(dirs[step] / "operations")
    if (run / "operations").exists():
        shutil.rmtree(run / "operations")

    operation_counts = {key: len(items) for key, items in sorted(grouped.items())}
    index = {
        "schema": "uklok.browser.operation-index/v1",
        "complete": False,
        "authority": "observed-only",
        "total_observed_calls": len(calls),
        "attributed_count": sum(bool(c["owner"]) for c in calls),
        "unattributed_count": sum(not c["owner"] for c in calls),
        "operation_counts": operation_counts,
        "operation_keys": sorted(operation_counts),
        "unattributed": [_provenance(c) for c in calls if not c["owner"]],
        "steps": {},
        "integrity": {"assembled_api_calls": len(calls), "grouped_call_count": sum(operation_counts.values()), "reconciles": sum(operation_counts.values()) == len(calls)},
    }
    for step_id in sorted(known_steps):
        step_calls = [c for c in calls if c["owner"] == step_id]
        keys = sorted(_group(step_calls))
        index["steps"][step_id] = {
            "total_observed_calls": len(step_calls),
            "grouped_operation_count": len(keys),
            "operation_keys": keys,
            "distinct_operations": [k for k in keys if len(steps_by_key.get(k, set())) == 1],
            "shared_operations": [k for k in keys if len(steps_by_key.get(k, set())) > 1],
            "credential_dependencies": _dependency_names(step_calls, deps),
        }
    _write(run, "operations/index.json", index)
    _secret_store(run, secrets)

    for step_id in selected:
        directory = dirs.get(step_id)
        if not directory:
            continue
        step_calls = [c for c in calls if c["owner"] == step_id]
        base = directory.relative_to(run) / "operations"
        keys = sorted(_group(step_calls))
        _write(run, str(base / "endpoints.json"), {"schema": "uklok.browser.operation-reading/v1", "step": step_id, "operations": _endpoint_records(step_calls, deps, steps_by_key)})
        _write(run, str(base / "auth.json"), {"schema": "uklok.browser.operation-auth/v1", "step": step_id, "dependencies": _dependency_names(step_calls, deps)})
        _write(run, str(base / "goal.json"), {
            "schema": "uklok.browser.operation-goal/v1",
            "step": step_id,
            "total_observed_calls": len(step_calls),
            "grouped_operation_count": len(keys),
            "operation_keys": keys,
            "distinct_operations": [k for k in keys if len(steps_by_key.get(k, set())) == 1],
            "shared_operations": [k for k in keys if len(steps_by_key.get(k, set())) > 1],
            "shared_with": {k: sorted(steps_by_key[k] - {step_id}) for k in keys if len(steps_by_key.get(k, set())) > 1},
        })
        gql, model = _graphql(step_calls, secrets)
        _write(run, str(base / "graphql" / "operations.json"), {"schema": "uklok.browser.graphql-operations/v1", "step": step_id, "operations": gql})
        _write(run, str(base / "graphql" / "observed-schema.json"), model)
        for host in sorted({_identity(c)["host"] for c in step_calls if _identity(c)["kind"] == "http"}):
            _write(run, str(base / "openapi" / (re.sub(r"[^A-Za-z0-9._-]+", "_", host) + ".json")), _openapi(host, step_calls, deps))

    verify_reading(run)
    if not index["integrity"]["reconciles"]:
        raise OperationReadError("operation counts did not reconcile")
    return index
