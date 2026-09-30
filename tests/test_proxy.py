"""The proxy view: forwarding, streaming, and its security boundaries."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from yarl import URL

from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import AUDIO, TOKEN

BASE = "/api/habird_proxy"


async def test_get_forwards_with_token_and_drops_browser_credentials(
    hass: HomeAssistant, proxy_entry, birdnet_go, hass_client
) -> None:
    client = await hass_client()
    resp = await client.get(
        BASE + "/api/v2/detections",
        params={"queryType": "search", "search": "Corvus corax"},
        headers={"Cookie": "session=browser-cookie"},
    )
    assert resp.status == 200
    assert await resp.json() == {
        "path": "/api/v2/detections",
        "query": {"queryType": "search", "search": "Corvus corax"},
    }
    seen = birdnet_go.requests[-1]
    # BirdNET-Go's token is added server-side; HA's token and the
    # browser's cookies never leave Home Assistant.
    assert seen.headers["Authorization"] == f"Bearer {TOKEN}"
    assert "Cookie" not in seen.headers


async def test_requires_home_assistant_login(
    hass: HomeAssistant, proxy_entry, birdnet_go, hass_client_no_auth
) -> None:
    client = await hass_client_no_auth()
    resp = await client.get(BASE + "/api/v2/analytics/species/summary")
    assert resp.status == 401
    assert birdnet_go.requests == []


async def test_only_birdnet_go_api_paths(
    hass: HomeAssistant, proxy_entry, birdnet_go, hass_client
) -> None:
    client = await hass_client()
    for path in (
        "/etc/passwd",
        "/api/v1/settings",
        "/api/v2/../../admin",
        "/api/v2/%2e%2e/%2e%2e/admin",
        "/api/v2/audio/%252e%252e",
        "/api/v2//double",
        "/api/v2/a\\b",
    ):
        resp = await client.get(URL(BASE + path, encoded=True))
        # 400 = HA's own security filter caught it first; either way it's refused.
        assert resp.status in (400, 403, 404), path
    assert birdnet_go.requests == []


async def test_range_requests_pass_through_for_audio_seeking(
    hass: HomeAssistant, proxy_entry, birdnet_go, hass_client
) -> None:
    client = await hass_client()
    resp = await client.get(BASE + "/api/v2/audio/42", headers={"Range": "bytes=100-199"})
    assert resp.status == 206
    assert resp.headers["Content-Range"] == f"bytes 100-199/{len(AUDIO)}"
    assert resp.headers["Accept-Ranges"] == "bytes"
    assert resp.headers["Content-Type"] == "audio/mpeg"
    assert await resp.read() == AUDIO[100:200]

    full = await client.get(BASE + "/api/v2/audio/42")
    assert full.status == 200
    assert await full.read() == AUDIO


async def test_live_stream_is_forwarded_as_it_arrives(
    hass: HomeAssistant, proxy_entry, birdnet_go, hass_client
) -> None:
    client = await hass_client()
    resp = await client.get(BASE + "/api/v2/detections/stream")
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "text/event-stream"
    # The first event must arrive while BirdNET-Go still holds the stream open.
    first = await asyncio.wait_for(resp.content.readuntil(b"\n\n"), timeout=5)
    assert b'"id": 1' in first
    birdnet_go.stream_gate.set()
    rest = await asyncio.wait_for(resp.read(), timeout=5)
    assert b'"id": 2' in rest


async def test_signed_url_works_for_the_live_stream(
    hass: HomeAssistant, proxy_entry, birdnet_go, hass_client_no_auth, hass_admin_credential, hass_access_token
) -> None:
    # A browser EventSource can't send headers, so the card asks HA for a
    # signed URL (auth/sign_path) instead.
    refresh_token = hass.auth.async_validate_access_token(hass_access_token)
    signed = async_sign_path(
        hass, BASE + "/api/v2/detections/stream", timedelta(minutes=5),
        refresh_token_id=refresh_token.id,
    )
    birdnet_go.stream_gate.set()
    client = await hass_client_no_auth()
    resp = await client.get(URL(signed, encoded=True))
    assert resp.status == 200
    assert b'"id": 1' in await resp.read()
    # The signature is an HA credential - it must not reach BirdNET-Go.
    assert "authSig" not in birdnet_go.requests[-1].query


async def test_review_write_needs_admin_and_gets_csrf(
    hass: HomeAssistant, proxy_entry, birdnet_go, hass_client, hass_read_only_access_token
) -> None:
    admin = await hass_client()
    resp = await admin.post(
        BASE + "/api/v2/detections/123/review",
        json={"verified": "false_positive", "correct": "false_positive"},
    )
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["got"]["verified"] == "false_positive"

    reader = await hass_client(hass_read_only_access_token)
    resp = await reader.post(BASE + "/api/v2/detections/123/review", json={"verified": "correct"})
    assert resp.status == 403

    # Nothing else is writable, even for an admin.
    before = len(birdnet_go.requests)
    for path in ("/api/v2/settings", "/api/v2/detections/123", "/api/v2/detections/1/review/x"):
        resp = await admin.post(BASE + path, json={})
        assert resp.status == 403, path
    resp = await admin.delete(BASE + "/api/v2/detections/123")
    assert resp.status == 405
    assert len(birdnet_go.requests) == before


async def test_read_only_users_can_read(
    hass: HomeAssistant, proxy_entry, birdnet_go, hass_client, hass_read_only_access_token
) -> None:
    reader = await hass_client(hass_read_only_access_token)
    resp = await reader.get(BASE + "/api/v2/analytics/species/summary")
    assert resp.status == 200


async def test_unreachable_birdnet_go_is_a_502(
    hass: HomeAssistant, proxy_entry, birdnet_go, hass_client
) -> None:
    await birdnet_go.server.close()
    client = await hass_client()
    resp = await client.get(BASE + "/api/v2/analytics/species/summary")
    assert resp.status == 502
    assert TOKEN not in await resp.text()


async def test_unloaded_entry_stops_forwarding(
    hass: HomeAssistant, proxy_entry, birdnet_go, hass_client
) -> None:
    assert await hass.config_entries.async_unload(proxy_entry.entry_id)
    client = await hass_client()
    resp = await client.get(BASE + "/api/v2/analytics/species/summary")
    assert resp.status == 503
    assert birdnet_go.requests == []
