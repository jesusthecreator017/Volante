# oso on a Raspberry Pi

Deploying both servers (this LAN Replay server and the public Arena server) on a Pi reached over Tailscale, with Arena public through Tailscale Funnel: [HANDOFF-tailscale.md](HANDOFF-tailscale.md).

Continuous deployment from `main` (Arena bundle and this LAN server, via a self-hosted runner): [CD.md](CD.md).

Everything server-side runs on one Raspberry Pi (tested target: Pi 5, Raspberry Pi OS Bookworm or later, 64-bit):

| What | Where on the Pi |
|---|---|
| Web app (built `app/dist`) | `https://<pi>.local:8443` |
| Session exchange: `imu.bin`, `meta.json`, `replay.bin` per session | `/api/sessions/...`, data in `/var/lib/oso/sessions/` |
| Fitbit: the Google refresh token + heart-rate proxy (Google Health API) | `/api/fitbit/...`, token in `/var/lib/oso/fitbit.json` (mode 600) |
| Plain-http listener for the sideloaded iPhone/Watch apps | `http://<pi>.local:8080` |

The server is `app/server/server.mjs`: plain Node (18+), no npm dependencies on the Pi.

## Deploy (from your laptop)

```bash
deploy/pi/deploy.sh pi@raspberrypi.local
```
It runs the web tests, builds `app/dist`, copies `dist` + `server` + the systemd unit to the Pi, and runs
`install.sh` there. That script installs Node, avahi (`<hostname>.local`), rsync and openssl if missing, creates the
`oso` system user, puts the app in `/opt/oso/app`, makes a self-signed certificate for `<hostname>.local` + the
Pi's IP on first run, and enables `oso.service`. Run `deploy.sh` again to update; sessions and the Fitbit token in
`/var/lib/oso` are never touched by a deploy.

```bash
ssh pi@raspberrypi.local 'systemctl status oso; journalctl -u oso -n 50'
curl http://raspberrypi.local:8080/api/health        # {"app":"oso","version":1}
```

## HTTPS and the certificate

Browsers only allow Web Bluetooth (the web app talking to the pod) on HTTPS, so the server listens on
https 8443 with the self-signed certificate in `/etc/oso/cert.pem`. Trust it once per device:
- **macOS:** `scp pi@raspberrypi.local:/etc/oso/cert.pem .`, open it in Keychain Access, set it to Always Trust.
- **Android / Chrome:** Settings → Security → Install a certificate → CA certificate.
- **iPhone:** not needed. The native app uses `http://<pi>.local:8080` (App Transport Security allows local
  networking); Safari has no Web Bluetooth anyway.

Alternatives: Tailscale (`tailscale cert` gives a real certificate for the Pi's tailnet name), or a real domain.

## Next to the Arena server

The same Pi also runs the public **Arena** server (its own agent and prompt), exposed through a Cloudflare Tunnel on
the website's domain, which owns `/api/...` there. This server is the **LAN-only** Replay side: it listens on its own
ports (8443/8080) and must **not** be added to the tunnel. It has no auth, and its `/api/sessions` and `/api/fitbit`
paths would collide with Arena's API on the same origin. Keep the ports distinct from whatever the Arena server
binds, and don't expose them on the router.

## API (no auth: keep the Pi on your own network)

```
GET  /api/health
GET  /api/sessions                                  list (meta + hasImu / hasReplay)
GET|PUT /api/sessions/<id>/{imu.bin|meta.json|replay.bin}
GET  /api/fitbit/status                             {"connected": bool}
POST /api/fitbit/token   {"clientId","refreshToken"} sent once by the iPhone app after Google sign-in
DELETE /api/fitbit/token
GET  /api/fitbit/heart-rate?from=<RFC 3339>&to=<RFC 3339>   [{"t": epoch s, "bpm": n}]
```
The refresh token is never served back. Anyone on the network can read heart rate through the proxy, which is
the same trust model as the session files.

## Video pipeline

`replay.bin` is still produced by the Python/Rust pipeline (`tools/README.md`). It can run on the Pi 5 (CPU only,
slow) or on a laptop that reads and writes `/var/lib/oso/sessions/<id>/` (e.g. over `sshfs`).

## Hackathon network notes

- Pi, phone and laptop must be on the same network. On a phone hotspot or venue Wi-Fi with client isolation, `.local`
  names and LAN traffic may be blocked: run your own travel router or the Pi as an access point.
- No `.local` resolution? Use the Pi's IP (`hostname -I` on the Pi) in the app's server setting.
