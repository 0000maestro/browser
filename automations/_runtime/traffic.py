from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

import aiohttp


STATE_SCHEMA = "uklok.browser.traffic-state/v1"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class CDPTrafficRecorder:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse, output: Path, state_file: Path):
        self.ws = ws
        self.output = output
        self.state_file = state_file
        self.next_id = 0
        self.sequence = 0
        self.lifecycle_seq = 0
        self.target_lifecycle_seq: dict[str, int] = {}
        self.target_counters: dict[str, dict[str, int]] = {}
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.target_to_session: dict[str, str] = {}
        self.session_to_target: dict[str, dict[str, Any]] = {}
        self.attaching: set[str] = set()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.active_requests: dict[str, dict[str, Any]] = {}
        self.open_streams: dict[str, dict[str, Any]] = {}
        self.counters = {
            "started": 0,
            "finished": 0,
            "failed": 0,
            "http_error": 0,
            "redirected": 0,
        }
        self.handle = output.open("w", encoding="utf-8", buffering=1)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    @staticmethod
    def _request_key(session_id: str | None, request_id: str | None) -> str:
        return f"{session_id or 'browser'}:{request_id or 'unknown'}"

    def _write_state(self) -> None:
        payload = {
            "schema": STATE_SCHEMA,
            "recorder_pid": os.getpid(),
            "updated_at": _now(),
            "latest_seq": self.sequence,
            "lifecycle_seq": self.lifecycle_seq,
            "target_lifecycle_seq": dict(self.target_lifecycle_seq),
            "counters": dict(self.counters),
            "target_counters": {key: dict(value) for key, value in self.target_counters.items()},
            "active_requests": list(self.active_requests.values()),
            "open_streams": list(self.open_streams.values()),
        }
        tmp = self.state_file.with_name(self.state_file.name + ".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        tmp.replace(self.state_file)



    def _target_id(self, session_id: str | None) -> str | None:
        target = self.session_to_target.get(session_id) or {}
        value = target.get("targetId")
        return str(value) if value else None

    def _bump_lifecycle(self, target_id: str | None) -> None:
        self.lifecycle_seq += 1
        if target_id:
            self.target_lifecycle_seq[target_id] = self.target_lifecycle_seq.get(target_id, 0) + 1

    def _increment_counter(self, name: str, target_id: str | None) -> None:
        self.counters[name] += 1
        if not target_id:
            return
        counters = self.target_counters.setdefault(
            target_id,
            {"started": 0, "finished": 0, "failed": 0, "http_error": 0, "redirected": 0},
        )
        counters[name] += 1


    def _drop_session(self, session_id: str) -> None:
        request_prefix = f"{session_id}:"
        removed_target_ids: set[str] = set()
        for key in [key for key in self.active_requests if key.startswith(request_prefix)]:
            value = self.active_requests.pop(key, None) or {}
            if value.get("target_id"):
                removed_target_ids.add(str(value["target_id"]))
        for key in [key for key in self.open_streams if key.startswith(request_prefix)]:
            value = self.open_streams.pop(key, None) or {}
            if value.get("target_id"):
                removed_target_ids.add(str(value["target_id"]))
        if removed_target_ids:
            for target_id in removed_target_ids:
                self._bump_lifecycle(target_id)
            self._write_state()

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self.next_id += 1
        message: dict[str, Any] = {"id": self.next_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id

        future = asyncio.get_running_loop().create_future()
        self.pending[self.next_id] = future
        await self.ws.send_json(message)
        response = await asyncio.wait_for(future, timeout=5)
        if "error" in response:
            raise RuntimeError(f"{method}: {response['error']}")
        return response.get("result", {})

    async def enable_network(self, session_id: str) -> None:
        try:
            await self.send("Network.enable", session_id=session_id)
        except Exception:
            return

    async def attach_target(self, target: dict[str, Any]) -> None:
        target_id = target.get("targetId")
        if not target_id or target.get("type") != "page":
            return
        if target_id in self.target_to_session or target_id in self.attaching:
            return

        self.attaching.add(target_id)
        try:
            result = await self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
            session_id = result.get("sessionId")
            if not session_id:
                return
            self.target_to_session[target_id] = session_id
            self.session_to_target[session_id] = target
            await self.enable_network(session_id)
        finally:
            self.attaching.discard(target_id)

    def _observe_network_state(self, raw: dict[str, Any]) -> None:
        method = raw.get("method", "")
        params = raw.get("params") or {}
        session_id = raw.get("sessionId")
        request_id = params.get("requestId")
        key = self._request_key(session_id, request_id)
        now = time.time()
        target_id = self._target_id(session_id)

        if method == "Network.requestWillBeSent":
            request = params.get("request") or {}
            if key in self.active_requests:
                self._increment_counter("redirected", target_id)
            self._increment_counter("started", target_id)
            self._bump_lifecycle(target_id)
            self.active_requests[key] = {
                "key": key,
                "session_id": session_id,
                "target_id": target_id,
                "request_id": request_id,
                "url": request.get("url"),
                "method": request.get("method"),
                "type": params.get("type") or "Other",
                "started_at": now,
                "response_status": None,
            }
            return

        if method == "Network.responseReceived":
            active = self.active_requests.get(key)
            if active is not None:
                status = (params.get("response") or {}).get("status")
                active["response_status"] = status
                if isinstance(status, (int, float)) and status >= 400 and not active.get("http_error_counted"):
                    active["http_error_counted"] = True
                    self._increment_counter("http_error", active.get("target_id"))
            return

        if method == "Network.loadingFinished":
            active = self.active_requests.pop(key, None) or {}
            active_target = active.get("target_id") or target_id
            self._increment_counter("finished", active_target)
            self._bump_lifecycle(active_target)
            return

        if method == "Network.loadingFailed":
            active = self.active_requests.pop(key, None) or {}
            active_target = active.get("target_id") or target_id
            self._increment_counter("failed", active_target)
            self._bump_lifecycle(active_target)
            return

        if method == "Network.webSocketCreated":
            self._bump_lifecycle(target_id)
            self.open_streams[key] = {
                "key": key,
                "session_id": session_id,
                "target_id": target_id,
                "request_id": request_id,
                "url": params.get("url"),
                "type": "WebSocket",
                "started_at": now,
            }
            return

        if method == "Network.webSocketClosed":
            stream = self.open_streams.pop(key, None) or {}
            self._bump_lifecycle(stream.get("target_id") or target_id)
            return

        if method == "Network.webTransportCreated":
            transport_id = params.get("transportId")
            transport_key = self._request_key(session_id, transport_id)
            self._bump_lifecycle(target_id)
            self.open_streams[transport_key] = {
                "key": transport_key,
                "session_id": session_id,
                "target_id": target_id,
                "request_id": transport_id,
                "url": params.get("url"),
                "type": "WebTransport",
                "started_at": now,
            }
            return

        if method == "Network.webTransportClosed":
            transport_id = params.get("transportId")
            transport_key = self._request_key(session_id, transport_id)
            stream = self.open_streams.pop(transport_key, None) or {}
            self._bump_lifecycle(stream.get("target_id") or target_id)

    def emit(self, raw: dict[str, Any]) -> None:
        self.sequence += 1
        session_id = raw.get("sessionId")
        envelope = {
            "seq": self.sequence,
            "captured_at": _now(),
            "target": self.session_to_target.get(session_id),
            "cdp": raw,
        }
        self.handle.write(json.dumps(envelope, separators=(",", ":"), ensure_ascii=False) + "\n")
        self._observe_network_state(raw)
        self._write_state()

    async def reader(self) -> None:
        async for message in self.ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                if message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                    break
                continue

            raw = json.loads(message.data)
            response_id = raw.get("id")
            if response_id in self.pending:
                future = self.pending.pop(response_id)
                if not future.done():
                    future.set_result(raw)
                continue

            method = raw.get("method", "")
            params = raw.get("params") or {}

            if method == "Target.targetCreated":
                self._spawn(self.attach_target(params.get("targetInfo") or {}))
                continue

            if method == "Target.attachedToTarget":
                session_id = params.get("sessionId")
                target = params.get("targetInfo") or {}
                target_id = target.get("targetId")
                if session_id and target.get("type") == "page":
                    existing = self.target_to_session.get(target_id) if target_id else None
                    if existing and existing != session_id:
                        self._spawn(self.send("Target.detachFromTarget", {"sessionId": session_id}))
                        continue
                    if target_id:
                        self.target_to_session[target_id] = session_id
                    self.session_to_target[session_id] = target
                    self._spawn(self.enable_network(session_id))
                continue

            if method == "Target.detachedFromTarget":
                session_id = params.get("sessionId")
                target = self.session_to_target.pop(session_id, None)
                if target and target.get("targetId"):
                    self.target_to_session.pop(target["targetId"], None)
                if session_id:
                    self._drop_session(session_id)
                continue

            if method.startswith("Network."):
                self.emit(raw)

        error = RuntimeError("CDP traffic observer disconnected")
        for future in self.pending.values():
            if not future.done():
                future.set_exception(error)
        self.pending.clear()

    async def setup(self) -> None:
        await self.send("Target.setDiscoverTargets", {"discover": True})
        targets = await self.send("Target.getTargets")
        for target in targets.get("targetInfos", []):
            if target.get("type") == "page":
                await self.attach_target(target)

        await self.send(
            "Target.setAutoAttach",
            {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
        )
        self._write_state()

    async def close(self) -> None:
        await self.ws.close()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.handle.close()


async def record(cdp_url: str, output: Path, state_file: Path, ready_file: Path) -> None:
    version_url = cdp_url.rstrip("/") + "/json/version"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    timeout = aiohttp.ClientTimeout(total=None, connect=5, sock_connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(version_url) as response:
            response.raise_for_status()
            version = await response.json()

        ws_url = version.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError(f"CDP endpoint did not expose webSocketDebuggerUrl: {version_url}")

        async with session.ws_connect(ws_url, heartbeat=20) as ws:
            recorder = CDPTrafficRecorder(ws, output, state_file)
            reader = asyncio.create_task(recorder.reader())
            try:
                await recorder.setup()
                ready_file.write_text("ready\n", encoding="utf-8")

                stop_task = asyncio.create_task(stop.wait())
                done, _ = await asyncio.wait({reader, stop_task}, return_when=asyncio.FIRST_COMPLETED)
                if reader in done and not stop.is_set():
                    await reader
                    raise RuntimeError("CDP traffic observer stopped before the automation completed")
                stop_task.cancel()
            finally:
                ready_file.unlink(missing_ok=True)
                await recorder.close()
                if not reader.done():
                    reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Observe raw Chrome Network.* CDP events without consuming Browser Harness events"
    )
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(record(args.cdp_url, args.output, args.state_file, args.ready_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
