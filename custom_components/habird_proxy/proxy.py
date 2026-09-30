"""The proxy view: <PROXY_PREFIX>/api/v2/... -> <BirdNET-Go>/api/v2/...

Security model:
- Only signed-in Home Assistant users can call it (requires_auth). The
  card authenticates with HA's own access token; the live-feed stream,
  which a browser EventSource can't attach a header to, uses an HA signed
  URL instead (auth/sign_path).
- Requests only ever go to the one BirdNET-Go configured in the config
  entry, and only to its /api/v2/ API. The path is validated, not just
  joined, so it can't be steered anywhere else.
- Reads (GET/HEAD) are open to any HA user. The only write forwarded is
  the card's detection review ("not it?"), and only for HA admins - the
  same bar HA ingress sets for the add-on.
- The browser's own credentials (HA token, cookies) are never forwarded.
  BirdNET-Go's API token, when configured, is added here, on the server,
  so it never reaches the browser.
"""

from __future__ import annotations

from http import HTTPStatus
import logging
import re
import secrets
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

from homeassistant.components.http import KEY_HASS_USER, HomeAssistantView
from homeassistant.components.http.auth import SIGN_QUERY_PARAM
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, PROXY_PREFIX

if TYPE_CHECKING:
    from . import ProxyTarget

_LOGGER = logging.getLogger(__name__)

# api/v2/ followed by plain path segments. No '.'/'..' segments, empty
# segments, backslashes or percent-escapes (aiohttp has already decoded the
# path once; a leftover '%' would mean double-encoding).
_SEGMENT = r"[A-Za-z0-9_\-~:@!$&'()*+,;=]+(?:\.[A-Za-z0-9_\-~:@!$&'()*+,;=]+)*"
_READ_PATH = re.compile(rf"api/v2(?:/{_SEGMENT})*/?")
# The one write the card makes: POST /api/v2/detections/<id>/review.
_WRITE_PATH = re.compile(r"api/v2/detections/[A-Za-z0-9_\-]+/review")
_STREAM_PATH = "api/v2/detections/stream"

# Request headers passed through to BirdNET-Go (everything else, notably
# Authorization and Cookie, is dropped).
_FORWARD_REQUEST = (
    "Accept",
    "Accept-Language",
    "Cache-Control",
    "Content-Type",
    "If-Modified-Since",
    "If-None-Match",
    "If-Range",
    "Last-Event-ID",
    "Range",
)
# Response headers passed back to the browser.
_FORWARD_RESPONSE = (
    "Accept-Ranges",
    "Cache-Control",
    "Content-Disposition",
    "Content-Encoding",
    "Content-Length",
    "Content-Range",
    "Content-Type",
    "ETag",
    "Last-Modified",
    "Retry-After",
)
_MAX_BODY = 64 * 1024
_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)
# The live feed stays open indefinitely; only connecting is bounded.
_STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=10, sock_read=None)


def _error(status: HTTPStatus, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


class BirdNetGoProxyView(HomeAssistantView):
    """Forward the card's BirdNET-Go API calls."""

    url = PROXY_PREFIX + "/{path:.*}"
    name = "api:habird_proxy"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Keep a handle on hass for the config entry's target."""
        self._hass = hass

    def target(self) -> ProxyTarget | None:
        """The configured BirdNET-Go, or None while no entry is loaded."""
        return self._hass.data.get(DOMAIN, {}).get("target")

    async def get(self, request: web.Request, path: str) -> web.StreamResponse:
        """Read from BirdNET-Go."""
        return await self._forward(request, path)

    async def head(self, request: web.Request, path: str) -> web.StreamResponse:
        """Read headers from BirdNET-Go."""
        return await self._forward(request, path)

    async def post(self, request: web.Request, path: str) -> web.StreamResponse:
        """Write to BirdNET-Go (detection reviews only)."""
        return await self._forward(request, path)

    async def _forward(self, request: web.Request, path: str) -> web.StreamResponse:
        target = self.target()
        if target is None:
            return _error(HTTPStatus.SERVICE_UNAVAILABLE, "Bird Card Proxy is not set up")

        write = request.method not in ("GET", "HEAD")
        if write:
            if not _WRITE_PATH.fullmatch(path):
                return _error(HTTPStatus.FORBIDDEN, "only detection reviews can be written")
            if not request[KEY_HASS_USER].is_admin:
                return _error(HTTPStatus.FORBIDDEN, "reviewing detections needs an admin user")
        elif not _READ_PATH.fullmatch(path):
            return _error(HTTPStatus.NOT_FOUND, "not a BirdNET-Go API path")

        headers = {h: request.headers[h] for h in _FORWARD_REQUEST if h in request.headers}
        if target.api_token:
            headers["Authorization"] = f"Bearer {target.api_token}"
        body = None
        if write:
            if request.content_length and request.content_length > _MAX_BODY:
                return _error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
            body = await request.content.read(_MAX_BODY + 1)
            if len(body) > _MAX_BODY:
                return _error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
            # BirdNET-Go's CSRF guard is double-submit: the X-CSRF-Token
            # header must match the csrf cookie. The browser's cookies stay
            # on this side, so mint a matching pair per request.
            csrf = secrets.token_urlsafe(24)
            headers["X-CSRF-Token"] = csrf
            headers["Cookie"] = f"csrf={csrf}"

        upstream_url = f"{target.url}/{path}"
        stream = path.rstrip("/") == _STREAM_PATH
        session = async_get_clientsession(self._hass, verify_ssl=target.verify_ssl)
        try:
            upstream = await session.request(
                request.method,
                upstream_url,
                # HA's signed-URL parameter is an HA credential - never forward it.
                params=[(k, v) for k, v in request.query.items() if k != SIGN_QUERY_PARAM],
                headers=headers,
                data=body,
                allow_redirects=False,
                auto_decompress=False,
                timeout=_STREAM_TIMEOUT if stream else _TIMEOUT,
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("BirdNET-Go request to /%s failed: %s", path, err)
            return _error(HTTPStatus.BAD_GATEWAY, "BirdNET-Go is unreachable from Home Assistant")

        try:
            response = web.StreamResponse(status=upstream.status)
            for h in _FORWARD_RESPONSE:
                if h in upstream.headers:
                    response.headers[h] = upstream.headers[h]
            if stream:
                # Keep reverse proxies in front of HA from buffering events.
                response.headers["X-Accel-Buffering"] = "no"
            await response.prepare(request)
            if request.method != "HEAD":
                async for chunk in upstream.content.iter_any():
                    await response.write(chunk)
            await response.write_eof()
        except (ConnectionResetError, aiohttp.ClientError, TimeoutError) as err:
            # The browser went away (closed the card, stopped a clip) or
            # BirdNET-Go dropped mid-response; nothing left to answer.
            _LOGGER.debug("BirdNET-Go response for /%s ended early: %s", path, err)
        finally:
            upstream.release()
        return response
