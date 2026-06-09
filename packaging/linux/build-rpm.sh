#!/usr/bin/env bash
# Build an .rpm package for netllm (Linux x86_64).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=rpm-version.sh
source "$(dirname "$0")/rpm-version.sh"
VERSION="${NETLLM_VERSION:-0.2.3.5}"
RPM_VERSION="$(netllm_rpm_version "$VERSION")"
RELEASE="${NETLLM_RPM_RELEASE:-1}"
STAGE="${ROOT}/packaging/linux/rpm-stage"
TOPDIR="${STAGE}/rpmbuild"

rm -rf "${STAGE}"
mkdir -p "${TOPDIR}"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

SRCROOT="${STAGE}/netllm-${RPM_VERSION}"
mkdir -p "${SRCROOT}"
rsync -a \
  --exclude=.git \
  --exclude=dist \
  --exclude=packaging/linux/stage \
  --exclude=packaging/linux/rpm-stage \
  --exclude=packaging/windows/stage \
  --exclude=.venv \
  "${ROOT}/" "${SRCROOT}/"

tar -czf "${TOPDIR}/SOURCES/netllm-${RPM_VERSION}.tar.gz" \
  -C "${STAGE}" "netllm-${RPM_VERSION}"

sed "s/@VERSION@/${RPM_VERSION}/g; s/@RELEASE@/${RELEASE}/g" \
  "${ROOT}/packaging/linux/netllm.spec.in" >"${TOPDIR}/SPECS/netllm.spec"

rpmbuild --define "_topdir ${TOPDIR}" -bb "${TOPDIR}/SPECS/netllm.spec"

mkdir -p "${ROOT}/dist"
find "${TOPDIR}/RPMS" -name '*.rpm' -exec cp {} "${ROOT}/dist/" \;
echo "RPM artifacts copied to ${ROOT}/dist/"
