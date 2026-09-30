"""Setting up and reconfiguring the proxy."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.habird_proxy.config_flow import normalize_url
from custom_components.habird_proxy.const import (
    CONF_API_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

from .conftest import TOKEN


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


def test_normalize_url() -> None:
    assert normalize_url("192.168.1.50:8080/") == "http://192.168.1.50:8080"
    assert normalize_url(" https://birds.example.com/bng/ ") == "https://birds.example.com/bng"
    assert normalize_url("ftp://x") is None
    assert normalize_url("http://") is None
    assert normalize_url("http://u:p@host") is None
    assert normalize_url("http://host/?q=1") is None


async def test_user_flow_creates_entry(hass: HomeAssistant, birdnet_go) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: birdnet_go.url + "/", CONF_VERIFY_SSL: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_URL: birdnet_go.url, CONF_API_TOKEN: "", CONF_VERIFY_SSL: True}


async def test_private_mode_needs_a_token(hass: HomeAssistant, private_birdnet_go) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: private_birdnet_go.url}
    )
    assert result["errors"] == {"base": "needs_token"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: private_birdnet_go.url, CONF_API_TOKEN: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: private_birdnet_go.url, CONF_API_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_TOKEN] == TOKEN


async def test_bad_url_and_unreachable_host(hass: HomeAssistant, birdnet_go) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "ftp://birds"}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}
    closed = birdnet_go.url
    await birdnet_go.server.close()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: closed})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_something_else_answering(hass: HomeAssistant, birdnet_go) -> None:
    # A web server that isn't BirdNET-Go's API (404 on the summary path).
    birdnet_go.missing_summary = True
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: birdnet_go.url}
    )
    assert result["errors"] == {"base": "not_birdnet_go"}


async def test_single_instance(hass: HomeAssistant, proxy_entry) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure(hass: HomeAssistant, proxy_entry, private_birdnet_go) -> None:
    result = await proxy_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: private_birdnet_go.url, CONF_API_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert proxy_entry.data[CONF_URL] == private_birdnet_go.url
    assert hass.data[DOMAIN]["target"].url == private_birdnet_go.url
