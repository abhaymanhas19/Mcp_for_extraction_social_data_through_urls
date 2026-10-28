import contextlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .config import load_config
from .csv_export import records_to_csv
from .errors import PublicError
from .js import js_json
from .mcp_server import build_mcp_server
from .service import ExportService
from .upstream import ProviderError
from .validation import validate_guid

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
EXPORT_PATH = re.compile(r"^/exports/([^/]+)\.(json|csv)\Z")


def log(entry: dict[str, Any]) -> None:
    print(json.dumps(entry), flush=True)


async def _send(send: Send, status: int, headers: dict[str, str], body: bytes = b"", more: bool = False) -> None:
    await send({"type": "http.response.start", "status": status, "headers": [(k.encode(), v.encode("latin-1")) for k, v in headers.items()]})
    await send({"type": "http.response.body", "body": body, "more_body": more})


async def _send_json(send: Send, status: int, value: Any) -> None:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    await _send(send, status, {"content-type": "application/json; charset=utf-8", "content-length": str(len(body)), "cache-control": "no-store"}, body)


async def _send_text(send: Send, status: int, value: str) -> None:
    body = value.encode()
    await _send(send, status, {"content-type": "text/plain; charset=utf-8", "content-length": str(len(body)), "cache-control": "no-store"}, body)


class HttpApp:
    def __init__(self) -> None:
        self.config = load_config()
        self.service = ExportService(self.config)
        self.session_manager = StreamableHTTPSessionManager(app=build_mcp_server(self.service), json_response=True, stateless=True)

    @contextlib.asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        async with self.session_manager.run():
            try:
                yield
            finally:
                await self.service.upstream.client.aclose()
                await self.service.metadata.client.aclose()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        started = {"value": False}

        async def tracked_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                started["value"] = True
            await send(message)

        try:
            await self._handle(scope, receive, tracked_send)
        except Exception as error:
            if started["value"]:
                raise
            if isinstance(error, PublicError):
                status = error.http_status
            elif isinstance(error, ProviderError):
                status = error.status if 400 <= error.status < 600 else 502
            else:
                status = 500
            message = error.message if isinstance(error, (PublicError, ProviderError)) else "Request failed."
            log({"level": "error", "status": status, "type": type(error).__name__})
            body: dict[str, Any] = {"error": message}
            if isinstance(error, PublicError):
                body["code"] = error.code
            payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            headers = {"content-type": "application/json; charset=utf-8", "content-length": str(len(payload)), "cache-control": "no-store"}
            if isinstance(error, ProviderError) and error.status == 429 and error.retry_after_seconds is not None:
                headers["retry-after"] = str(error.retry_after_seconds)
            await _send(send, status, headers, payload)

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        stack = contextlib.AsyncExitStack()
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await stack.enter_async_context(self.lifespan())
                except Exception as error:
                    await send({"type": "lifespan.startup.failed", "message": str(error)})
                    return
                log({"level": "info", "event": "server_started", "port": self.config.port})
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await stack.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return

    def _allowed(self, scope: Scope) -> bool:
        host = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == b"host"), "")
        return host.lower() in self.config.allowed_hosts

    async def _handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._allowed(scope):
            await _send_json(send, 403, {"error": "Host is not allowed."})
            return
        path = scope.get("path", "/")
        if path == "/healthz":
            await _send_json(send, 200, {"status": "ok"})
            return
        if path == "/.well-known/openai-apps-challenge":
            token = (os.environ.get("OPENAI_APPS_CHALLENGE") or "").strip()
            if not token:
                await _send_json(send, 404, {"error": "Challenge is not configured."})
                return
            await _send_text(send, 200, token)
            return
        if path == "/mcp":
            await self.session_manager.handle_request(scope, receive, send)
            return
        match = EXPORT_PATH.match(path)
        if match:
            await self._serve_export(scope, send, match.group(1), match.group(2))
            return
        await _send_json(send, 404, {"error": "Not found."})

    async def _serve_export(self, scope: Scope, send: Send, job_id: str, extension: str) -> None:
        method = scope["method"]
        if method not in ("GET", "HEAD"):
            await _send(send, 405, {"allow": "GET, HEAD"})
            return
        guid = validate_guid(job_id)
        is_csv = extension == "csv"
        headers = {
            "content-type": "text/csv; charset=utf-8" if is_csv else "application/json; charset=utf-8",
            "content-disposition": f'attachment; filename="evidano-social-export-{guid}.{extension}"',
            "cache-control": "private, no-store", "x-content-type-options": "nosniff",
        }
        if method == "HEAD":
            job = await self.service.get_job(guid)
            if job["state"] != "completed":
                failed = job["state"] == "failed"
                raise PublicError("export_failed" if failed else "job_in_progress", "export did not complete successfully" if failed else "export is still running", 422 if failed else 409)
            await _send(send, 200, headers)
            return
        records = await self.service.get_all_records(guid)
        if is_csv:
            await _send(send, 200, headers, records_to_csv(records).encode())
            return
        await _send(send, 200, headers, b"[", more=True)
        for index, record in enumerate(records):
            chunk = f"{',' if index else ''}\n{js_json(record)}"
            await send({"type": "http.response.body", "body": chunk.encode(), "more_body": True})
        await send({"type": "http.response.body", "body": b"\n]\n", "more_body": False})


def create_app() -> HttpApp:
    logging.basicConfig(level=logging.WARNING)
    return HttpApp()
