import math
import os

from .js import JsUrl
from .types import AppConfig


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _api_key() -> str:
    value = _env("UPSTREAM_API_KEY") or _env("KEY")
    if not value:
        raise RuntimeError("UPSTREAM_API_KEY or KEY is required")
    return value


def _number(raw: str) -> float:
    text = raw.strip()
    if not text:
        return 0.0
    if "_" in text:
        return math.nan
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return float(int(text, 0)) if text[:2].lower() in ("0x", "0o", "0b") else math.nan
    except ValueError:
        return math.nan


def _integer(name: str, fallback: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return fallback
    value = _number(raw)
    if not math.isfinite(value) or not value.is_integer() or value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be an integer between {minimum} and {maximum}")
    return int(value)


def _https_url(name: str, raw: str) -> JsUrl:
    url = JsUrl(raw)
    if url.protocol != "https:":
        raise RuntimeError(f"{name} must use HTTPS")
    return url


def load_config() -> AppConfig:
    public_base_url = JsUrl(_env("PUBLIC_BASE_URL") or "https://socialmedia.evidano.com")
    if public_base_url.protocol not in ("http:", "https:"):
        raise RuntimeError("PUBLIC_BASE_URL must use HTTP or HTTPS")
    configured = [x.strip().lower() for x in (os.environ.get("ALLOWED_HOSTS") or "").split(",") if x.strip()]
    vercel_host = _env("VERCEL_URL").lower()
    allowed_hosts = configured or [public_base_url.host.lower()]
    if vercel_host and vercel_host not in allowed_hosts:
        allowed_hosts.append(vercel_host)
    return AppConfig(
        upstream_base_url=_https_url("UPSTREAM_API_BASE_URL", _env("UPSTREAM_API_BASE_URL") or "https://exportcomments.com"),
        upstream_api_key=_api_key(),
        public_base_url=public_base_url,
        port=_integer("PORT", 3000, 1, 65535),
        allowed_hosts=frozenset(allowed_hosts),
        upstream_timeout_ms=_integer("UPSTREAM_TIMEOUT_MS", 30_000, 1_000, 120_000),
        wait_poll_ms=_integer("WAIT_POLL_MS", 5_000, 250, 10_000),
    )
