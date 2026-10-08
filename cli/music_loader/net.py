"""Small HTTP helpers on top of urllib (thread-safe, size-capped)."""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import __version__

USER_AGENT = f"music-loader/{__version__} (https://github.com/skrpld/music-loader)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_DEFAULT_MAX_BYTES = 8 * 1024 * 1024


class ResponseTooLarge(Exception):
    pass


@dataclass
class Response:
    status: int
    body: bytes
    url: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8", "replace"))


def probe(url: str, timeout: float = 10.0) -> int:
    """HTTP status of a GET over http(s), without reading the body: the answer
    is the proof that the host can be reached. Any status is returned; network
    errors (DNS, refused, timeout, TLS, a broken answer) raise OSError."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in {"http", "https"}:
        raise OSError(f"refusing non-http(s) URL: {url[:80]}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        exc.close()
        return exc.code
    except http.client.HTTPException as exc:
        raise OSError(f"{type(exc).__name__}: {exc}") from exc


def http_get(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> Response:
    """GET over http(s) only. HTTP error statuses are returned, network
    errors raise OSError."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in {"http", "https"}:
        raise OSError(f"refusing non-http(s) URL: {url[:80]}")
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise ResponseTooLarge(f"response larger than {max_bytes} bytes")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResponseTooLarge(f"response larger than {max_bytes} bytes")
            return Response(response.status, body, response.geturl())
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(64 * 1024)
        except OSError:
            body = b""
        return Response(exc.code, body, url)
    except ResponseTooLarge as exc:
        raise OSError(str(exc)) from exc
