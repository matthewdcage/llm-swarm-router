#!/usr/bin/env bash
# Build a .deb package for netllm (Linux amd64).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${NETLLM_VERSION:-0.2.0}"
ARCH="${NETLLM_DEB_ARCH:-amd64}"
STAGE="${ROOT}/packaging/linux/stage"
PKGROOT="${STAGE}/netllm_${VERSION}_${ARCH}"

rm -rf "${STAGE}"
mkdir -p "${PKGROOT}/usr/lib/netllm" "${PKGROOT}/usr/bin"
mkdir -p "${PKGROOT}/usr/lib/systemd/user"
mkdir -p "${PKGROOT}/DEBIAN"

uv sync --directory "${ROOT}"
uv pip install --python "${ROOT}/.venv/bin/python" \
  -e "${ROOT}" --no-dev \
  --target "${PKGROOT}/usr/lib/netllm"

cat >"${PKGROOT}/usr/bin/netllm" <<'EOF'
#!/usr/bin/env bash
exec /usr/lib/netllm/bin/netllm "$@"
EOF
chmod 755 "${PKGROOT}/usr/bin/netllm"

install -m 644 "${ROOT}/packaging/linux/netllm.service" \
  "${PKGROOT}/usr/lib/systemd/user/netllm.service"

cat >"${PKGROOT}/DEBIAN/control" <<EOF
Package: netllm
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: netllm contributors
Description: Mesh router for local LLM backends
 Depends: python3 (>= 3.11)
EOF

cat >"${PKGROOT}/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi
EOF
chmod 755 "${PKGROOT}/DEBIAN/postinst"

mkdir -p "${ROOT}/dist"
dpkg-deb --build "${PKGROOT}" "${ROOT}/dist/netllm_${VERSION}_${ARCH}.deb"
echo "Built ${ROOT}/dist/netllm_${VERSION}_${ARCH}.deb"
