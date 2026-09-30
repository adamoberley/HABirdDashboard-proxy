"""Bird Card Proxy: reach BirdNET-Go through Home Assistant.

Bird Card (https://github.com/adamoberley/HABirdDashboard) normally talks
to BirdNET-Go straight from the browser, which only works where the
browser can reach BirdNET-Go - usually the LAN. This integration serves
BirdNET-Go's API on Home Assistant's own origin, so the card works
anywhere Home Assistant does: remote access, VPNs, HTTPS pages.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, DOMAIN
from .proxy import BirdNetGoProxyView


@dataclass(frozen=True)
class ProxyTarget:
    """The one BirdNET-Go the proxy forwards to."""

    url: str
    api_token: str
    verify_ssl: bool


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Point the proxy at the configured BirdNET-Go."""
    data = hass.data.setdefault(DOMAIN, {})
    # Views can't be unregistered, so register once per HA run; with no
    # loaded entry the view answers 503 (see BirdNetGoProxyView.target).
    if not data.get("view_registered"):
        hass.http.register_view(BirdNetGoProxyView(hass))
        data["view_registered"] = True
    data["target"] = ProxyTarget(
        url=entry.data[CONF_URL],
        api_token=entry.data.get(CONF_API_TOKEN, ""),
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, True),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop forwarding."""
    hass.data.get(DOMAIN, {}).pop("target", None)
    return True
