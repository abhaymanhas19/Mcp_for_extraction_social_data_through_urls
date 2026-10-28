import asyncio
import json
import math
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .js import JsUrl, encode_uri_component, is_finite_num, js_json
from .types import AppConfig, JsonRecord
from .validation import assert_safe_result_url


class ProviderError(Exception):
    def __init__(self, message: str, status: int, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after_seconds = retry_after_seconds


def _public_provider_error(status: int) -> str:
    if status == 400:
        return "The export request was rejected. Check the URL, export mode, and whether another export is already running."
    if status == 403:
        return "The export service could not authorize the request."
    if status == 404:
        return "The export job was not found."
    if status == 409:
        return "The export is not ready yet."
    if status == 429:
        return "Export capacity is temporarily exceeded."
    return "The export service is temporarily unavailable." if status >= 500 else f"The export request failed with HTTP {status}."


def _header_date(header: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(header)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(header.strip())
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _retry_seconds(payload: Any, header: str | None) -> int | None:
    if header:
        try:
            value = float(header.strip()) if header.strip() else 0.0
        except ValueError:
            value = math.nan
        if math.isfinite(value) and value >= 0:
            return math.ceil(value)
        date = _header_date(header)
        if date:
            return max(0, math.ceil((date - datetime.now(timezone.utc)).total_seconds()))
    strings: list[str] = []
    if isinstance(payload, str):
        strings.append(payload)
    elif isinstance(payload, dict):
        for key in ("seconds_to_wait", "retry_after", "retryAfter"):
            value = payload.get(key)
            if is_finite_num(value) and value >= 0:
                return math.ceil(value)
        strings.extend(v for v in payload.values() if isinstance(v, str))
    for value in strings:
        match = re.search(r"(?:available|retry|try again)[^0-9]{0,20}([0-9]+)\s*(seconds?|minutes?)", value, re.IGNORECASE)
        if match:
            return int(match.group(1)) * (60 if match.group(2).lower().startswith("min") else 1)
    return None


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _js_float(text: str) -> int | float:
    value = float(text)
    return int(value) if value.is_integer() and abs(value) <= 2 ** 53 else value


def _now_ms() -> float:
    return time.monotonic() * 1000


class UpstreamClient:
    def __init__(self, config: AppConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.client = client or httpx.AsyncClient(follow_redirects=False, timeout=None)

    async def _request(self, path: str, method: str = "GET", body: str | None = None, timeout_ms: float | None = None) -> Any:
        deadline = _now_ms() + (self.config.upstream_timeout_ms if timeout_ms is None else timeout_ms)
        retries = 2 if method == "GET" else 0
        attempt = 0
        while True:
            try:
                return await self._request_once(path, method, body, max(250, deadline - _now_ms()))
            except ProviderError as error:
                retryable = error.status in RETRYABLE_STATUS
                wait = error.retry_after_seconds * 1000 if error.retry_after_seconds is not None else 400 * 2 ** attempt + random.random() * 300
                if not retryable or attempt >= retries or wait > 5_000 or _now_ms() + wait + 250 > deadline:
                    raise
                await asyncio.sleep(wait / 1000)
            attempt += 1

    async def _request_once(self, path: str, method: str, body: str | None, timeout_ms: float) -> Any:
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                url = JsUrl(path, self.config.upstream_base_url)
                hop = 0
                while True:
                    same_origin = url.origin == self.config.upstream_base_url.origin
                    headers = {"Accept": "application/json"}
                    if same_origin:
                        headers["X-AUTH-TOKEN"] = self.config.upstream_api_key
                    if body:
                        headers["Content-Type"] = "application/json"
                    response = await self.client.send(self.client.build_request(method, url.href, headers=headers, content=body), stream=True)
                    location = response.headers.get("location")
                    if response.status_code < 300 or response.status_code >= 400 or not location or hop >= 3:
                        break
                    next_url = JsUrl(location, url)
                    if next_url.protocol != "https:":
                        break
                    await response.aclose()
                    url = next_url
                    hop += 1
                try:
                    text = (await response.aread()).decode("utf-8", "replace")
                finally:
                    await response.aclose()
            payload: Any = None
            if text:
                try:
                    payload = json.loads(text, parse_float=_js_float)
                except ValueError:
                    payload = text
            if not 200 <= response.status_code < 300:
                raise ProviderError(
                    _public_provider_error(response.status_code),
                    response.status_code,
                    _retry_seconds(payload, response.headers.get("retry-after")),
                )
            return payload
        except ProviderError:
            raise
        except TimeoutError:
            raise ProviderError("export provider timed out", 504) from None
        except Exception:
            raise ProviderError("export provider is unavailable", 502) from None

    async def create_job(self, url: str, options: JsonRecord, timeout_ms: float | None = None) -> JsonRecord:
        limit = self.config.upstream_timeout_ms if timeout_ms is None else min(self.config.upstream_timeout_ms, timeout_ms)
        return await self._request("/api/v3/job", "POST", js_json({"url": url, "options": options}), limit)

    async def get_job(self, guid: str, timeout_ms: float | None = None) -> JsonRecord:
        return await self._request(f"/api/v3/job/{encode_uri_component(guid)}", timeout_ms=timeout_ms)

    async def get_records(self, job: JsonRecord) -> list[JsonRecord]:
        if not job.get("json_url"):
            raise ProviderError("The export completed without a usable result file. The source may have no publicly accessible records.", 409)
        safe_url = assert_safe_result_url(job["json_url"], self.config.upstream_base_url)
        payload = await self._request(safe_url.href)
        if not isinstance(payload, list):
            raise ProviderError("export data is unavailable", 502)
        records: list[JsonRecord] = []
        for item in payload:
            if isinstance(item, dict):
                records.append(item)
            elif isinstance(item, list):
                records.extend(nested for nested in item if isinstance(nested, dict))
        return records
