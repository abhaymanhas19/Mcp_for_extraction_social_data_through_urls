import re
import unicodedata
from typing import Any

from .js import MISSING, js_json, js_trim
from .types import JsonRecord

_NAMED = {"amp": "&", "quot": '"', "apos": "'", "lt": "<", "gt": ">", "nbsp": " "}
_ENTITY = re.compile(r"&(?:amp|quot|apos|lt|gt|nbsp|#[0-9]+|#x[0-9a-f]+);", re.IGNORECASE)
_CONTROLS = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")
_ARTIFACTS = re.compile("[​﻿]")
_NOISE = re.compile(r"^(?:giveaway|analytics)_")


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _is_blank(value: Any) -> bool:
    return value is None or value is MISSING or value == "" or (isinstance(value, (list, dict)) and len(value) == 0)


def _decode_entity(match: re.Match[str]) -> str:
    entity = match.group(0)
    body = entity[1:-1]
    if body[0] == "#":
        is_hex = body[1:2].lower() == "x"
        point = int(body[2 if is_hex else 1:], 16 if is_hex else 10)
        if 0 <= point <= 0x10FFFF and not 0xD800 <= point <= 0xDFFF:
            return chr(point)
        return entity
    return _NAMED.get(body.lower(), entity)


def _clean_value(value: Any, counters: dict[str, int], forbidden: re.Pattern[str] | None) -> Any:
    if isinstance(value, str):
        if forbidden and forbidden.search(value):
            counters["removedProviderReferences"] += 1
            return None
        cleaned = value
        without_controls = _CONTROLS.sub("", cleaned)
        if without_controls != cleaned:
            counters["removedControlCharacterValues"] += 1
            cleaned = without_controls
        without_artifacts = _ARTIFACTS.sub("", cleaned)
        if without_artifacts != cleaned:
            counters["removedInvisibleCharacterValues"] += 1
            cleaned = without_artifacts
        decoded = _ENTITY.sub(_decode_entity, cleaned)
        if decoded != cleaned:
            counters["decodedHtmlEntityValues"] += 1
            cleaned = decoded
        normalized = unicodedata.normalize("NFC", re.sub(r"\r\n?", "\n", cleaned))
        if normalized != cleaned:
            counters["normalizedStringValues"] += 1
            cleaned = normalized
        trimmed = js_trim(cleaned)
        if trimmed != cleaned:
            counters["trimmedStringValues"] += 1
            cleaned = trimmed
        return cleaned
    if isinstance(value, list):
        return [_clean_value(item, counters, forbidden) for item in value]
    if _is_record(value):
        return {key: _clean_value(item, counters, forbidden) for key, item in value.items()}
    return value


def clean_records_for_output(rows: list[JsonRecord], forbidden_hosts: list[str] | None = None) -> tuple[list[JsonRecord], dict[str, Any]]:
    counters = {"normalizedStringValues": 0, "trimmedStringValues": 0, "decodedHtmlEntityValues": 0, "removedControlCharacterValues": 0, "removedInvisibleCharacterValues": 0, "removedProviderReferences": 0}
    hosts = [h for h in (re.sub(r"^www\.", "", h) for h in (forbidden_hosts or [])) if h]
    forbidden = re.compile(rf"^(?:https?:)?//(?:[^/?#]*\.)?(?:{'|'.join(re.escape(h) for h in hosts)})(?:[/:?#]|\Z)", re.IGNORECASE) if hosts else None
    records = [_clean_value(row, counters, forbidden) for row in rows]
    all_rows: list[JsonRecord] = []

    def collect(items: list[JsonRecord]) -> None:
        for row in items:
            all_rows.append(row)
            if isinstance(row.get("replies"), list):
                collect([r for r in row["replies"] if _is_record(r)])

    collect(records)
    fields = list(dict.fromkeys(field for row in all_rows for field in row))
    removed_always_blank = [f for f in fields if all(f not in row or _is_blank(row[f]) for row in all_rows)]
    removed = set(removed_always_blank)
    removed_duplicates: list[dict[str, str]] = []
    noise_fields = [f for f in fields if _NOISE.match(f) and f not in removed]
    canonical_fields = [f for f in fields if not _NOISE.match(f) and f not in removed]
    for field in noise_fields:
        base = _NOISE.sub("", field)
        duplicate_of = next((c for c in canonical_fields if c == base and all(_stable(row.get(field, MISSING)) == _stable(row.get(c, MISSING)) for row in records)), None)
        if duplicate_of:
            removed.add(field)
            removed_duplicates.append({"field": field, "duplicateOf": duplicate_of})

    def strip(row: JsonRecord) -> JsonRecord:
        return {
            field: [strip(r) if _is_record(r) else r for r in value] if field == "replies" and isinstance(value, list) else value
            for field, value in row.items() if field not in removed
        }

    quality = {"removedAlwaysBlankFields": removed_always_blank, "removedDuplicateFields": removed_duplicates, **counters}
    return [strip(row) for row in records], quality


def _stable(value: Any) -> str:
    return "undefined" if value is MISSING else js_json(value)
