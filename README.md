# Bird Card Proxy

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

An optional companion to [Bird Card](https://github.com/adamoberley/HABirdDashboard)
that lets the card reach **BirdNET-Go through Home Assistant**. With it,
recordings, live detections and the "not it?" review button work anywhere
Home Assistant works: over Nabu Casa, on a VPN, behind a reverse proxy, or
from an `https://` dashboard.

## Do I need it?

Bird Card normally talks to BirdNET-Go **straight from your browser**. That
works when your browser can reach BirdNET-Go's address, which usually means
being at home on the same network.

Away from home, the card falls back to Home Assistant's MQTT history. Your
birds still show, but recordings can't play, because audio never travels
over MQTT.

| Your setup | Without the proxy | With the proxy |
|---|---|---|
| At home, `http://` dashboard | Everything works | Everything works |
| Home Assistant OS with the BirdNET-Go add-on, `https://` or Nabu Casa | Works through add-on ingress (admin users) | Works for every user |
| Home Assistant in Docker / Core, away from home | Birds via MQTT, **no recordings** | Everything works |
| On a VPN where BirdNET-Go's hostname doesn't resolve | Birds via MQTT, **no recordings** | Everything works |

If everything already works for you, you don't need this.

## Install

1. In HACS, open the three-dot menu → **Custom repositories**. Add
   `https://github.com/adamoberley/HABirdDashboard-proxy` with type
   **Integration**.
2. Install **Bird Card Proxy** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Bird Card Proxy**.
4. Enter the address **Home Assistant itself** uses to reach BirdNET-Go, for example:
   - `http://192.168.1.50:8080` (BirdNET-Go on another machine or in Docker)
   - `http://db21ed7f-birdnet-go:8080` (the BirdNET-Go add-on on Home Assistant OS)
5. If BirdNET-Go's **Private Mode** is on, paste an API token from
   BirdNET-Go's settings. It stays in Home Assistant and is never sent to
   the browser.

That's it. Bird Card **v1.7.0 or newer** detects the proxy automatically
and routes everything through it. To keep a card talking to BirdNET-Go
directly, set `proxy: false` in that card's YAML.

To change the address or token later, open the integration and choose
**Reconfigure**.

## How it works

The integration serves BirdNET-Go's API on Home Assistant's own address, at
`/api/habird_proxy/api/v2/...`. The card calls that instead of BirdNET-Go,
and Home Assistant forwards each request to the BirdNET-Go you configured.

- **Only signed-in Home Assistant users** can use it. The card sends your
  normal Home Assistant login. The live-detection stream, which browsers
  can't attach a login header to, uses a short-lived Home Assistant signed
  URL instead.
- **It only ever talks to your BirdNET-Go**, and only to its `/api/v2/`
  API. Paths are validated, so it can't be used to reach anything else on
  your network.
- **It's read-only, with one exception:** the card's "not it?" detection
  review, which only admin users can send (the same rule as Home Assistant
  add-on ingress). Settings and everything else in BirdNET-Go can't be
  changed through it.
- **Your Home Assistant login and cookies are never forwarded** to
  BirdNET-Go. The BirdNET-Go token, if you set one, is added by Home
  Assistant on the way out.
- **Recordings stream through**, including seeking (HTTP `Range`
  requests), and the live-detection stream is passed on as each event
  arrives.

## Troubleshooting

- **"Home Assistant couldn't reach that address."** The address has to
  work from the Home Assistant machine, not from your laptop. In Docker,
  `localhost` means Home Assistant's own container, so use the host's
  LAN IP or the BirdNET-Go container's name on a shared Docker network.
- **"BirdNET-Go asked for sign-in (Private Mode)."** Create an API token
  in BirdNET-Go's settings and enter it.
- **The card still can't play recordings remotely.** Make sure the card
  is v1.7.0 or newer, and that its YAML doesn't set `proxy: false`.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest -q
```

The tests run Home Assistant's real HTTP stack against a fake BirdNET-Go on
localhost. They cover login, path validation, token handling, `Range`
passthrough, streaming, signed URLs and the admin-only review write.

## License

[MIT](LICENSE)
