import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from .js import JsUrl, is_num, js_json, num_str, js_trim
from .types import JsonRecord

ID_KEYS = ["comment_id", "id", "rpid", "review_id", "cid", "reply_id", "tweet_id", "status_id", "message_id"]
CONTEXT_KEYS = ["video_id", "channel_id", "post_id", "post_url", "post_title", "media_id", "shortcode", "thread_id", "subreddit", "product_id", "app_id", "listing_id"]
TIME_KEY = re.compile(r"^(?:time|timestamp|date|created|created_at|created_utc|created_time|published|published_at|updated_at)\Z", re.IGNORECASE)
URL_KEY = re.compile(r"^(?:url|permalink|link|comment_url|post_url)\Z", re.IGNORECASE)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == "None"


def _row_key(row: JsonRecord) -> str:
    for key in ID_KEYS:
        value = row.get(key)
        if (isinstance(value, str) and value and value != "None") or is_num(value):
            return f"{key}:{value if isinstance(value, str) else num_str(value)}"
    return f"row:{js_json({k: v for k, v in row.items() if k != 'replies'})}"


def _epoch_iso(value: Any) -> str | None:
    if is_num(value):
        text = num_str(math.trunc(value)) if math.isfinite(value) else num_str(value)
    elif isinstance(value, str):
        text = js_trim(value)
    else:
        text = ""
    if not re.fullmatch(r"[0-9]{10}(?:\.[0-9]+)?|[0-9]{13}", text):
        return None
    ms = int(text) if len(text) == 13 else math.trunc(float(text) * 1000)
    if ms < 978_307_200_000 or ms > 4_102_444_800_000:
        return None
    moment = _EPOCH + timedelta(milliseconds=ms)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


@dataclass
class _Node:
    key: str
    row: JsonRecord
    depth: int
    parent: str | None
    order: int
    had_replies: bool


def normalize_threads(rows: list[JsonRecord], platform: str | None, source_url: str | None) -> tuple[list[JsonRecord], dict[str, Any]]:
    summary: dict[str, Any] = {"duplicateRecordsRemoved": 0, "duplicateRepliesRemoved": 0, "replies": 0, "filledContextValues": 0, "clearedInvalidValues": 0, "absolutizedUrls": 0, "addedIsoTimeFields": []}
    origin = ""
    try:
        if source_url:
            origin = JsUrl(source_url).origin
    except ValueError:
        pass

    kept: dict[str, _Node] = {}
    roots: list[str] = []
    order = 0

    def visit(items: list[Any], parent: str | None, depth: int) -> None:
        nonlocal order
        for item in items:
            if not isinstance(item, dict):
                continue
            key = _row_key(item)
            replies = item["replies"] if isinstance(item.get("replies"), list) else None
            row = {k: v for k, v in item.items() if k != "replies"}
            previous = kept.get(key)
            if previous is None:
                kept[key] = _Node(key, row, depth, parent, order, replies is not None)
                order += 1
                if depth == 0:
                    roots.append(key)
            else:
                if depth == 0 and previous.depth == 0:
                    summary["duplicateRecordsRemoved"] += 1
                else:
                    summary["duplicateRepliesRemoved"] += 1
                if depth > previous.depth:
                    if previous.depth == 0:
                        roots.remove(key)
                    kept[key] = replace(previous, row=row, depth=depth, parent=parent, had_replies=previous.had_replies or replies is not None)
                elif replies is not None:
                    previous.had_replies = True
            if replies:
                visit(replies, key, depth + 1)

    visit(rows, None, 0)

    children: dict[str, list[_Node]] = {}
    for node in kept.values():
        if node.parent is None or node.parent == node.key:
            continue
        children.setdefault(node.parent, []).append(node)
    for items in children.values():
        items.sort(key=lambda n: n.order)

    iso_fields: dict[str, None] = {}

    def finish(row: JsonRecord, parent_row: JsonRecord | None) -> JsonRecord:
        out: JsonRecord = {}
        for key, value in row.items():
            next_value = value
            if URL_KEY.match(key) and isinstance(value, str) and value.startswith("/") and not value.startswith("//") and origin:
                next_value = f"{origin}{value}"
                summary["absolutizedUrls"] += 1
            out[key] = next_value
            if TIME_KEY.match(key):
                iso = _epoch_iso(value)
                if iso and f"{key}_iso" not in row:
                    out[f"{key}_iso"] = iso
                    iso_fields[f"{key}_iso"] = None
        if parent_row is not None:
            for key in CONTEXT_KEYS:
                if _blank(out.get(key)) and not _blank(parent_row.get(key)):
                    out[key] = parent_row[key]
                    summary["filledContextValues"] += 1
        if platform == "youtube" and isinstance(out.get("channel_id"), str) and out.get("channel_id") == out.get("video_id"):
            out["channel_id"] = None
            summary["clearedInvalidValues"] += 1
        return out

    replies_field = any(isinstance(r, dict) and "replies" in r for r in rows)
    seen: set[str] = set()

    def build(node: _Node, parent_row: JsonRecord | None, depth: int) -> JsonRecord:
        seen.add(node.key)
        row = finish(node.row, parent_row)
        kids = [child for child in children.get(node.key, []) if child.key not in seen]
        if depth > 0:
            summary["replies"] += 1
        if node.had_replies or kids:
            row["replies"] = [build(child, row, depth + 1) for child in kids]
        elif replies_field:
            row["replies"] = None
        return row

    records = [build(kept[key], None, 0) for key in roots]
    for node in list(kept.values()):
        if node.key not in seen:
            records.append(build(node, None, 0))
    summary["addedIsoTimeFields"] = list(iso_fields)
    return records, summary
