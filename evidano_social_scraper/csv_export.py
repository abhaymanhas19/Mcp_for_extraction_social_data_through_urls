import re
from typing import Any

from .js import is_num, js_json, js_str, js_trim
from .types import JsonRecord

ID_KEYS = ["comment_id", "id", "rpid", "review_id", "cid", "reply_id", "tweet_id", "status_id", "message_id"]
NUMERIC = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
FORMULA = re.compile(r"^[=+\-\t\r]")
AT_CALL = re.compile(r"^@[A-Za-z_][A-Za-z0-9_.]*\s*\(")


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or is_num(value):
        return js_str(value)
    return js_json(value)


def _safe_cell(value: Any) -> str:
    text = _flatten(value)
    if isinstance(value, str) and not NUMERIC.match(js_trim(text)) and (FORMULA.match(text) or AT_CALL.match(text)):
        text = f"'{text}"
    return '"' + text.replace('"', '""') + '"'


def _id_of(record: JsonRecord) -> str:
    for key in ID_KEYS:
        value = record.get(key)
        if (isinstance(value, str) and value) or is_num(value):
            return js_str(value)
    return ""


def _rows_of(records: list[JsonRecord]) -> list[JsonRecord]:
    threaded = any(isinstance(r.get("replies"), list) and r["replies"] for r in records)
    if not threaded:
        out = []
        for record in records:
            replies = record.get("replies")
            rest = {k: v for k, v in record.items() if k != "replies"}
            if not (isinstance(replies, list) or replies is None):
                rest["replies"] = replies
            out.append(rest)
        return out
    rows: list[JsonRecord] = []

    def walk(items: list[Any], parent: JsonRecord | None, root: str, depth: int) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            replies = item.get("replies")
            rest = {k: v for k, v in item.items() if k != "replies"}
            row_id = _id_of(rest)
            root_id = row_id if depth == 0 else root
            rows.append({"row_type": "comment" if depth == 0 else "reply", "reply_to_id": _id_of(parent) if parent is not None else "", "thread_root_id": root_id, "thread_depth": depth, **rest})
            if isinstance(replies, list):
                walk(replies, rest, root_id, depth + 1)

    walk(records, None, "", 0)
    return rows


def records_to_csv(records: list[JsonRecord]) -> str:
    rows = _rows_of(records)
    columns = list(dict.fromkeys(key for row in rows for key in row))
    if not columns:
        return ""
    lines = [",".join(_safe_cell(c) for c in columns)]
    for row in rows:
        lines.append(",".join(_safe_cell(row.get(c)) for c in columns))
    return "﻿" + "\r\n".join(lines) + "\r\n"
