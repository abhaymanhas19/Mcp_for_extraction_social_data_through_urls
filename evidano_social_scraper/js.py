import json
import math
import re
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit

JS_WHITESPACE = "\t\n\v\f\r                  　﻿"
MISSING = object()


def js_trim(value: str) -> str:
    return value.strip(JS_WHITESPACE)


def is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_finite_num(value: Any) -> bool:
    return is_num(value) and math.isfinite(value)


def js_round(value: float) -> int:
    return math.floor(value + 0.5)


def num_str(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "0"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    text = repr(value)
    if "e" not in text:
        return text
    mantissa, exponent = text.split("e")
    power = int(exponent)
    if -7 < power < 21:
        return format(Decimal(text), "f")
    return f"{mantissa}e{'+' if power > 0 else '-'}{abs(power)}"


def js_str(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if is_num(value):
        return num_str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ",".join("" if item is None else js_str(item) for item in value)
    return "[object Object]"


def js_json(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if is_num(value):
        return num_str(value) if math.isfinite(value) else "null"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(js_json(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(str(k), ensure_ascii=False)}:{js_json(v)}" for k, v in value.items() if v is not MISSING) + "}"
    return "null"


def encode_uri_component(value: str) -> str:
    return _percent(value, lambda ch: ch.isascii() and (ch.isalnum() or ch in "-_.!~*'()"))


def _percent(value: str, keep) -> str:
    out = []
    for ch in value:
        if keep(ch):
            out.append(ch)
        else:
            out.extend(f"%{byte:02X}" for byte in ch.encode("utf-8", "replace"))
    return "".join(out)


def _encode_set(value: str, extra: str) -> str:
    return _percent(value, lambda ch: 0x20 < ord(ch) < 0x7F and ch not in extra)


def form_encode(value: str) -> str:
    out = []
    for byte in value.encode("utf-8", "replace"):
        ch = chr(byte)
        if byte == 0x20:
            out.append("+")
        elif ch.isascii() and (ch.isalnum() or ch in "*-._"):
            out.append(ch)
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def _remove_dot_segments(path: str) -> str:
    segments = path.split("/")[1:]
    out: list[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        lower = segment.lower()
        if lower in (".", "%2e"):
            if last:
                out.append("")
        elif lower in ("..", ".%2e", "%2e.", "%2e%2e"):
            if out:
                out.pop()
            if last:
                out.append("")
        else:
            out.append(segment)
    return "/" + "/".join(out)


SPECIAL_PORTS = {"http": "80", "https": "443", "ws": "80", "wss": "443", "ftp": "21"}
PATH_SET = ' "#<>?`{}'
QUERY_SET = " \"#<>'"
FRAGMENT_SET = ' "<>`'


class JsUrl:
    def __init__(self, raw: str, base: "JsUrl | str | None" = None):
        text = re.sub(r"[\t\n\r]", "", raw).strip("".join(chr(i) for i in range(0x21)))
        if base is not None:
            text = urljoin(base.href if isinstance(base, JsUrl) else str(base), text)
        match = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", text)
        if not match:
            raise ValueError("Invalid URL")
        scheme = match.group(1).lower()
        self.protocol = f"{scheme}:"
        self.username = ""
        self.password = ""
        self.port = ""
        self._hostname = ""
        self._query: str | None = None
        self._fragment: str | None = None
        if scheme not in SPECIAL_PORTS:
            self.pathname = text[len(match.group(0)):]
            self._special = False
            return
        self._special = True
        cut = re.search(r"[?#]", text)
        head = text[: cut.start()] if cut else text
        text = head.replace("\\", "/") + text[len(head):]
        parts = urlsplit(text)
        if not parts.netloc or not parts.hostname:
            raise ValueError("Invalid URL")
        port = parts.port
        self.username = parts.username or ""
        self.password = parts.password or ""
        self._hostname = parts.hostname.lower()
        self.port = "" if port is None or str(port) == SPECIAL_PORTS[scheme] else str(port)
        self.pathname = _encode_set(_remove_dot_segments(parts.path or "/"), PATH_SET)
        fragment_at = text.find("#")
        query_at = text.find("?")
        if query_at != -1 and (fragment_at == -1 or query_at < fragment_at):
            self._query = _encode_set(parts.query, QUERY_SET)
        if fragment_at != -1:
            self._fragment = _encode_set(parts.fragment, FRAGMENT_SET)

    @property
    def hostname(self) -> str:
        return self._hostname

    @hostname.setter
    def hostname(self, value: str) -> None:
        self._hostname = value.lower()

    @property
    def host(self) -> str:
        return f"{self._hostname}:{self.port}" if self.port else self._hostname

    @property
    def origin(self) -> str:
        return f"{self.protocol}//{self.host}" if self._special else "null"

    @property
    def search(self) -> str:
        return f"?{self._query}" if self._query else ""

    @search.setter
    def search(self, value: str) -> None:
        value = value[1:] if value.startswith("?") else value
        self._query = _encode_set(value, QUERY_SET) if value else None

    @property
    def hash(self) -> str:
        return f"#{self._fragment}" if self._fragment else ""

    @hash.setter
    def hash(self, value: str) -> None:
        value = value[1:] if value.startswith("#") else value
        self._fragment = _encode_set(value, FRAGMENT_SET) if value else None

    def _params(self) -> list[tuple[str, str]]:
        return parse_qsl(self._query or "", keep_blank_values=True, errors="replace")

    def _write_params(self, params: list[tuple[str, str]]) -> None:
        serialized = "&".join(f"{form_encode(k)}={form_encode(v)}" for k, v in params)
        self._query = serialized or None

    def param_keys(self) -> list[str]:
        return [key for key, _ in self._params()]

    def param_get(self, key: str) -> str | None:
        return next((v for k, v in self._params() if k == key), None)

    def param_delete(self, key: str) -> None:
        self._write_params([(k, v) for k, v in self._params() if k != key])

    def param_set(self, key: str, value: str) -> None:
        params, found = [], False
        for k, v in self._params():
            if k != key:
                params.append((k, v))
            elif not found:
                params.append((k, value))
                found = True
        if not found:
            params.append((key, value))
        self._write_params(params)

    @property
    def href(self) -> str:
        if not self._special:
            return f"{self.protocol}{self.pathname}"
        userinfo = ""
        if self.username or self.password:
            userinfo = self.username + (f":{self.password}" if self.password else "") + "@"
        query = f"?{self._query}" if self._query is not None else ""
        fragment = f"#{self._fragment}" if self._fragment is not None else ""
        return f"{self.protocol}//{userinfo}{self.host}{self.pathname}{query}{fragment}"

    def __str__(self) -> str:
        return self.href
