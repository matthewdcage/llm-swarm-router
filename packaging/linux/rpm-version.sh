#!/usr/bin/env bash
# Map semver to an RPM-safe version string for paths and spec Version.
#
# Rules:
# - RPM Version rejects hyphens (use dots for prerelease: 0.0.0-dev -> 0.0.0.dev)
# - Never use ~ (bash tilde-expands ~user inside double-quoted paths and breaks tar)
netllm_rpm_version() {
  local version="$1"
  echo "${version//-/.}"
}
