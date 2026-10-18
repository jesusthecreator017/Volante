#!/bin/bash
# oso Arena install/update (idempotent). Run as root on the Pi:
#   sudo deploy/install.sh /path/to/oso-arena-pi        (the verified bundle: site/, viewer/, kit/)
# Never touches the database or the uploaded files (the web service creates/migrates the schema at start-up).
# Never touches server B (oso.service, /opt/oso, /var/lib/oso, /etc/oso) or avahi.
set -euo pipefail
BUNDLE=$(realpath "${1:?usage: install.sh <oso-arena-pi bundle dir>}")
SRC=$(realpath "$(dirname "$0")/..")
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
[ -f "$BUNDLE/kit/oso_arena-0.4.0-py3-none-any.whl" ] && [ -d "$BUNDLE/site" ] && [ -d "$BUNDLE/viewer" ] || { echo "not a bundle: $BUNDLE"; exit 1; }
(cd "$BUNDLE" && sha256sum --quiet -c SHA256SUMS) || { echo "bundle checksum mismatch"; exit 1; }

echo "== users"
getent group oso-arena-sandbox >/dev/null || groupadd --system oso-arena-sandbox
id oso-arena-sandbox >/dev/null 2>&1 || useradd --system --gid oso-arena-sandbox --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin oso-arena-sandbox
id oso-arena >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/oso-arena --no-create-home --shell /usr/sbin/nologin oso-arena
usermod -a -G oso-arena-sandbox oso-arena   # reads what the sandbox writes in its job dir

echo "== directories"
install -d -m 0755 -o root -g root /opt/oso-arena /opt/oso-arena/bundle
install -d -m 0750 -o oso-arena -g oso-arena-sandbox /var/lib/oso-arena          # sandbox may traverse, not list secrets
install -d -m 0700 -o oso-arena -g oso-arena /var/lib/oso-arena/db /var/lib/oso-arena/replays /var/lib/oso-arena/quarantine
install -d -m 2750 -o oso-arena -g oso-arena-sandbox /var/lib/oso-arena/models /var/lib/oso-arena/logs /var/lib/oso-arena/jobs
install -d -m 0700 -o oso-arena -g oso-arena /etc/oso-arena /var/backups/oso-arena

echo "== python venv (pinned)"
[ -x /opt/oso-arena/venv/bin/python ] || python3 -m venv /opt/oso-arena/venv
/opt/oso-arena/venv/bin/pip install -q --upgrade pip
/opt/oso-arena/venv/bin/pip install -q -r "$SRC/deploy/requirements.lock"
/opt/oso-arena/venv/bin/pip install -q --no-deps --force-reinstall "$BUNDLE/kit/oso_arena-0.4.0-py3-none-any.whl"
STAGE=$(mktemp -d)   # build from a copy so the checkout gets no root-owned build/ files
cp -a "$SRC/osoarena" "$SRC/pyproject.toml" "$STAGE/"
/opt/oso-arena/venv/bin/pip install -q --no-deps --force-reinstall "$STAGE"
rm -rf "$STAGE"
/opt/oso-arena/venv/bin/python -c "import oso_arena, osoarena.web; assert oso_arena.KIT_VERSION == '0.4.0', oso_arena.KIT_VERSION"

echo "== bundle (site, viewer)"
for d in site viewer contract; do
  rm -rf "/opt/oso-arena/bundle/$d.new"
  cp -a "$BUNDLE/$d" "/opt/oso-arena/bundle/$d.new"
  rm -rf "/opt/oso-arena/bundle/$d"
  mv "/opt/oso-arena/bundle/$d.new" "/opt/oso-arena/bundle/$d"
done
chown -R root:root /opt/oso-arena/bundle && chmod -R a+rX,go-w /opt/oso-arena/bundle

echo "== env file"
ENV=/etc/oso-arena/arena.env
if [ ! -f $ENV ]; then
  HOSTNAME_TS=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' || true)
  umask 077
  cat > $ENV <<EOF
# oso Arena settings (mode 600, owner oso-arena). Secrets live only here.
OSO_ARENA_BASE_URL=https://${HOSTNAME_TS:-CHANGE-ME.ts.net}
OSO_ARENA_SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
# D2: the GitHub OAuth app (callback: \$OSO_ARENA_BASE_URL/auth/github/callback). Sign-in is off until both are set.
OSO_ARENA_GITHUB_CLIENT_ID=
OSO_ARENA_GITHUB_CLIENT_SECRET=
OSO_ARENA_COOKIE_SECURE=1
OSO_ARENA_HSTS=1
OSO_ARENA_SANDBOX=systemd
# Tailscale Funnel sets X-Forwarded-For on proxied requests; trusted only from loopback
OSO_ARENA_CLIENT_IP_HEADER=X-Forwarded-For
# D6: tournament "server time" (Sunday 18:00 in this zone)
TZ=America/New_York
# base_40min.safetensors when it arrives (enables House Base and the base-hash upload rule)
OSO_ARENA_BASE_MODEL=
EOF
fi
chown oso-arena:oso-arena $ENV && chmod 600 $ENV

echo "== systemd + polkit"
install -m 0644 "$SRC"/deploy/systemd/oso-arena-*.service "$SRC"/deploy/systemd/oso-arena-*.timer /etc/systemd/system/
install -d -m 0755 /etc/polkit-1/rules.d
install -m 0644 "$SRC/deploy/50-oso-arena.rules" /etc/polkit-1/rules.d/50-oso-arena.rules
install -m 0755 "$SRC/deploy/oso-arena-admin" /usr/local/bin/oso-arena-admin
install -m 0755 "$SRC/deploy/oso-arena-ctl" /usr/local/bin/oso-arena-ctl
systemctl daemon-reload
systemctl try-restart polkit.service || true
systemctl enable oso-arena-web.service oso-arena-supervisor@1.service oso-arena-supervisor@2.service oso-arena-scheduler.service oso-arena-backup.timer
systemctl restart oso-arena-web.service
sleep 2
systemctl restart oso-arena-supervisor@1.service oso-arena-supervisor@2.service oso-arena-scheduler.service
systemctl start oso-arena-backup.timer
sleep 2
curl -fsS http://127.0.0.1:8701/healthz && echo && echo "== installed"
