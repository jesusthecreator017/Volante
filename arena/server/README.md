# Arena server (runs on the Raspberry Pi)

The oso / Volante Arena backend: the HTTP API and site host (`osoarena/web.py`), the upload pipeline, two supervisors that run every
untrusted model inside a sandboxed `oso-arena-child@<job>` systemd unit, the match scheduler, ladder (Elo), tournaments, replays and an
admin CLI. The contract it serves is `arena/handoff/PROMPT.md`; the site is `site/`, the 3D viewer `web/arena/`.

This folder was imported (with its history) from the Pi's `~/oso-arena-server`, where it was developed. It is now the source of truth:
change it here, by PR, then deploy.

## Deploy to the Pi
1. Build the bundle (site, viewer, kit, base model) on a Mac: `arena/handoff/make_bundle.sh` -> `dist/oso-arena-pi/`.
2. Copy this folder and the bundle to the Pi (as `itsfrosty`), e.g.
   `rsync -a arena/server/ pi:~/oso-arena-server/` and `rsync -a dist/oso-arena-pi/ pi:~/arena-bundle/oso-arena-pi-<name>/`.
3. As root: `sudo bash ~/oso-arena-server/deploy/install.sh ~/arena-bundle/oso-arena-pi-<name>`. It is safe to re-run: it keeps
   `/etc/oso-arena/arena.env` (secrets: session key, GitHub OAuth app, the House Base path) and the data in `/var/lib/oso-arena`,
   reinstalls the code and restarts the services.

Secrets, the database, uploads, replays and backups live only on the Pi (`/etc/oso-arena`, `/var/lib/oso-arena`,
`/var/backups/oso-arena`) and must never be committed here (`.gitignore` covers `*.env` and `.dev/`).

## Tests
- `tests/test_twins.py` (Volante Twins, section 14 acceptance), `tests/test_signin.py`, `tests/test_pods.py`: in-process (FastAPI TestClient, throwaway data dir): `pip install -r deploy/requirements.lock pytest httpx` plus
  the kit wheel, then `python -m pytest -q tests/test_pods.py`.
- `tests/test_contract.py`, `tests/smoke_pipeline.py`, `tests/tournament_test.py`: against a running dev server (see each file's header).
