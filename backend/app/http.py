"""Shared outbound HTTP helpers.

`client()` is a plain sync httpx client. `proxied_client()` routes through the
gluetun HTTP proxy so the request egresses from the VPN IP — required for MAM
(the session cookie is IP-bound) and good hygiene for any tracker traffic.
"""
from __future__ import annotations

import httpx

from .config import settings


def client(timeout: float = 20.0, **kw) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True, **kw)


def proxied_client(timeout: float = 20.0, **kw) -> httpx.Client:
    proxy = settings.GLUETUN_PROXY_URL or None
    return httpx.Client(timeout=timeout, follow_redirects=True, proxy=proxy, **kw)
