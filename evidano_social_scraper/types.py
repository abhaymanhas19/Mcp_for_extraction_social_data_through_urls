from dataclasses import dataclass
from typing import Any

from .js import JsUrl

EXPORT_TYPES = (
    "comments", "reviews", "posts", "videos", "search", "listings", "events", "mentions",
    "discussions", "messages", "members", "likes", "shares", "followers", "following",
    "tweets", "live_chat", "facebook_ads",
)

JsonRecord = dict[str, Any]


@dataclass(frozen=True)
class AppConfig:
    upstream_base_url: JsUrl
    upstream_api_key: str
    public_base_url: JsUrl
    port: int
    allowed_hosts: frozenset[str]
    upstream_timeout_ms: int
    wait_poll_ms: int


@dataclass(frozen=True)
class StartExportInput:
    url: str
    export_type: str
    include_replies: bool
    wait_seconds: int
    advanced_mode: bool
    limit: int | None = None
