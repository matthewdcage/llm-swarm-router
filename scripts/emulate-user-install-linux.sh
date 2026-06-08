#!/usr/bin/env bash
# Build and install like an end user: deb package → systemd user service.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(uv run --directory "$ROOT" python -c "import tomllib; print(tomllib.load(open('$ROOT/pyproject.toml','rb'))['project']['version'])")"

echo "==> Linux install rehearsal for netllm"
echo "    Requires: dpkg-deb, systemd user session"
echo

echo "==> Building .deb"
chmod +x "$ROOT/packaging/linux/build-deb.sh"
NETLLM_VERSION="$VERSION" "$ROOT/packaging/linux/build-deb.sh"

DEB="$(ls -1 "$ROOT/dist/"*.deb | head -1)"
echo "==> Installing $DEB"
sudo dpkg -i "$DEB" || sudo apt-get install -f -y

echo "==> Enabling systemd user service"
systemctl --user daemon-reload
systemctl --user enable --now netllm

sleep 2
echo "==> Status"
netllm status || true

cat <<EOF

Done. Verify:
  - Browser: http://127.0.0.1:11400/ui/
  - Terminal: netllm status
  - Logs: journalctl --user -u netllm -f

To repeat:
  $ROOT/scripts/emulate-user-install-linux.sh

EOF
