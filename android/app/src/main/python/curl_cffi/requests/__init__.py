"""curl_cffi.requests on top of requests, without browser impersonation."""
from __future__ import annotations

import requests as _requests

from . import exceptions
from .exceptions import HTTPError, RequestException

Response = _requests.Response
# A Literal of browser names in curl_cffi; only used in annotations.
BrowserTypeLiteral = str

# Arguments requests understands; curl_cffi-only ones (impersonate, ...) are dropped.
_REQUEST_ARGS = {
    "params", "data", "headers", "cookies", "files", "auth", "timeout", "allow_redirects",
    "proxies", "hooks", "stream", "verify", "cert", "json",
}


# (connect, read) seconds. requests has no default and would wait on a stalled connection
# forever; curl_cffi's is 30 s, and a connection that is not up after 10 s is not coming
# (spotapi retries every request, so a longer wait multiplies).
DEFAULT_TIMEOUT = (10, 30)


class Session(_requests.Session):
    def __init__(self, *args, impersonate=None, timeout=DEFAULT_TIMEOUT, **kwargs):
        super().__init__()
        self.impersonate = impersonate
        self.timeout = timeout
        for key in ("headers", "cookies", "proxies"):
            value = kwargs.get(key)
            if value:
                getattr(self, key).update(value)

    def request(self, method, url, *args, **kwargs):
        kwargs = {key: value for key, value in kwargs.items() if key in _REQUEST_ARGS}
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().request(method, url, *args, **kwargs)


def request(method, url, **kwargs):
    with Session() as session:
        return session.request(method, url, **kwargs)


def get(url, **kwargs):
    return request("GET", url, **kwargs)


def post(url, **kwargs):
    return request("POST", url, **kwargs)


__all__ = ["BrowserTypeLiteral", "DEFAULT_TIMEOUT", "HTTPError", "Response", "Session", "RequestException", "exceptions",
           "request", "get", "post"]
