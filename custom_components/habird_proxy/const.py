"""Constants for the Bird Card Proxy integration."""

DOMAIN = "habird_proxy"

CONF_URL = "url"
CONF_API_TOKEN = "api_token"
CONF_VERIFY_SSL = "verify_ssl"

# The card calls <PROXY_PREFIX>/api/v2/... on Home Assistant's own origin.
PROXY_PREFIX = "/api/habird_proxy"

# Used to check a BirdNET-Go URL (and token) during setup. The card reads
# this endpoint on every refresh, so it exists on every supported version.
PROBE_PATH = "/api/v2/analytics/species/summary"
