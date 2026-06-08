#!/usr/bin/env bash
# Build an .rpm package for netllm (Linux x86_64).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${NETLLM_VERSION:-0.2.1}"
RELEASE="${NETLLM_RPM_RELEASE:-1}"
STAGE="${ROOT}/packaging/linux/rpm-stage"
TOPDIR="${STAGE}/rpmbuild"

rm -rf "${STAGE}"
mkdir -p "${TOPDIR}"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

tar -czf "${TOPDIR}/SOURCES/netllm-${VERSION}.tar.gz" \
  --exclude=.git --exclude=dist --exclude=packaging/linux/stage \
  -C "${ROOT}" .

sed "s/@VERSION@/${VERSION}/g; s/@RELEASE@/${RELEASE}/g" \
  "${ROOT}/packaging/linux/netllm.spec.in" >"${TOPDIR}/SPECS/netllm.spec"

rpmbuild --define "_topdir ${TOPDIR}" -bb "${TOPDIR}/SPECS/netllm.spec"

mkdir -p "${ROOT}/dist"
find "${TOPDIR}/RPMS" -name '*.rpm' -exec cp {} "${ROOT}/dist/" \;
echo "RPM artifacts copied to ${ROOT}/dist/"
