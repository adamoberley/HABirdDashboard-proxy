"""Fixtures: a fake BirdNET-Go on localhost, and a loaded proxy entry."""

from __future__ import annotations

import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.habird_proxy.const import (
    CONF_API_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

TOKEN = "bng-secret-token"
AUDIO = bytes(range(256)) * 40  # 10240 bytes of fake clip


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""


class FakeBirdNetGo:
    """Just enough BirdNET-Go: records what it receives."""

    def __init__(self, require_token: bool = False) -> None:
        self.require_token = require_token
        self.missing_summary = False
        self.requests: list[web.Request] = []
        self.bodies: list[bytes] = []
        self.stream_gate = asyncio.Event()
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self.server = TestServer(app, host="127.0.0.1")

    @property
    def url(self) -> str:
        return str(self.server.make_url("")).rstrip("/")

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.requests.append(request)
        self.bodies.append(await request.read())
        if self.require_token and request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.json_response({"error": "unauthorized"}, status=401)
        path = request.path
        if path == "/api/v2/analytics/species/summary" and self.missing_summary:
            return web.Response(status=404, text="not found")
        if path == "/api/v2/analytics/species/summary":
            return web.json_response([{"scientific_name": "Corvus corax", "count": 3}])
        if path.startswith("/api/v2/audio/"):
            rng = request.headers.get("Range")
            if rng and rng.startswith("bytes="):
                start, _, end = rng[6:].partition("-")
                start, end = int(start), int(end or len(AUDIO) - 1)
                return web.Response(
                    status=206,
                    body=AUDIO[start : end + 1],
                    headers={
                        "Content-Type": "audio/mpeg",
                        "Accept-Ranges": "bytes",
                        "Content-Range": f"bytes {start}-{end}/{len(AUDIO)}",
                    },
                )
            return web.Response(body=AUDIO, content_type="audio/mpeg", headers={"Accept-Ranges": "bytes"})
        if path == "/api/v2/detections/stream":
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await resp.write(b"event: detection\ndata: {\"id\": 1}\n\n")
            # Hold the stream open until the test has seen the first event,
            # proving the proxy forwards chunks as they arrive.
            await self.stream_gate.wait()
            await resp.write(b"event: detection\ndata: {\"id\": 2}\n\n")
            return resp
        if request.method == "POST" and path.endswith("/review"):
            ok = request.headers.get("X-CSRF-Token") and request.headers.get(
                "X-CSRF-Token"
            ) == request.cookies.get("csrf")
            if not ok:
                return web.json_response({"error": "csrf"}, status=403)
            return web.json_response({"ok": True, "got": json.loads(self.bodies[-1])})
        return web.json_response({"path": path, "query": dict(request.query)})


@pytest.fixture
async def birdnet_go(socket_enabled):
    fake = FakeBirdNetGo()
    await fake.server.start_server()
    yield fake
    fake.stream_gate.set()
    await fake.server.close()


@pytest.fixture
async def private_birdnet_go(socket_enabled):
    fake = FakeBirdNetGo(require_token=True)
    await fake.server.start_server()
    yield fake
    await fake.server.close()


@pytest.fixture
async def proxy_entry(hass, birdnet_go):
    """The proxy configured against the fake BirdNET-Go, with a token."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="birdnet",
        data={CONF_URL: birdnet_go.url, CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
