"""Config flow: which BirdNET-Go to proxy to."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, DOMAIN, PROBE_PATH


def normalize_url(raw: str) -> str | None:
    """Return 'scheme://host[:port][/base]' without a trailing slash, or None."""
    raw = raw.strip()
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.query or parts.fragment or parts.username or parts.password:
        return None
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


async def probe(hass, url: str, token: str, verify_ssl: bool) -> str | None:
    """Check BirdNET-Go answers. Returns an error key, or None when fine."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    try:
        async with session.get(
            url + PROBE_PATH,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                return "invalid_auth" if token else "needs_token"
            if resp.status != HTTPStatus.OK:
                return "not_birdnet_go"
            try:
                await resp.json(content_type=None)
            except ValueError:
                return "not_birdnet_go"
    except aiohttp.ClientSSLError:
        return "ssl_error"
    except (aiohttp.ClientError, TimeoutError):
        return "cannot_connect"
    return None


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            vol.Optional(
                CONF_API_TOKEN, default=defaults.get(CONF_API_TOKEN, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Optional(
                CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)
            ): bool,
        }
    )


class BirdCardProxyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up (or change) the BirdNET-Go the proxy talks to."""

    VERSION = 1

    async def _validate(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        url = normalize_url(user_input[CONF_URL])
        if url is None:
            return None, {CONF_URL: "invalid_url"}
        data = {
            CONF_URL: url,
            CONF_API_TOKEN: user_input.get(CONF_API_TOKEN, "").strip(),
            CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, True),
        }
        if err := await probe(self.hass, url, data[CONF_API_TOKEN], data[CONF_VERIFY_SSL]):
            return None, {"base": err}
        return data, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for BirdNET-Go's address (and token, for Private Mode)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await self._validate(user_input)
            if data is not None:
                return self.async_create_entry(
                    title=urlsplit(data[CONF_URL]).netloc, data=data
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address, token or SSL setting."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await self._validate(user_input)
            if data is not None:
                return self.async_update_reload_and_abort(
                    entry, title=urlsplit(data[CONF_URL]).netloc, data=data
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or dict(entry.data)),
            errors=errors,
        )
