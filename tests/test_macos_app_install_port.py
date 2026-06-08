"""Regression: macOS BSD sed does not treat \\s as whitespace."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

# Mirrors agent_listen_port() in packaging/scripts/macos-app-install.sh
_PORT_FROM_CONFIG = r"""
config="$1"
listen="127.0.0.1:11400"
if [[ -f "$config" ]]; then
  listen="$(
    grep -E '^[[:space:]]*listen[[:space:]]*=' "$config" 2>/dev/null | head -1 \
      | sed -E 's/^[[:space:]]*listen[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/' \
      || echo "127.0.0.1:11400"
  )"
fi
port="${listen##*:}"
port="${port//\"/}"
if [[ -z "$port" || "$port" == "$listen" ]]; then
  port="11400"
fi
echo "$port"
"""


def _port_from_config(config_text: str) -> str:
    config = Path(tempfile.gettempdir()) / "netllm-test-config.toml"
    config.write_text(config_text, encoding="utf-8")
    result = subprocess.run(
        ["bash", "-c", _PORT_FROM_CONFIG, "_", str(config)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_listen_port_parsed_without_trailing_quote() -> None:
    port = _port_from_config('[agent]\nlisten = "127.0.0.1:11400"\n')
    assert port == "11400"


def test_listen_port_custom_value() -> None:
    port = _port_from_config('[agent]\nlisten = "0.0.0.0:11401"\n')
    assert port == "11401"


def test_bsd_sed_backslash_s_leaves_trailing_quote() -> None:
    """Document the bug: \\s in sed -E fails on macOS, leaving port=11400\"."""
    config = Path(tempfile.gettempdir()) / "netllm-test-broken-config.toml"
    config.write_text('[agent]\nlisten = "127.0.0.1:11400"\n', encoding="utf-8")
    broken = r"""
config="$1"
listen="$(
  grep -E '^\s*listen\s*=' "$config" 2>/dev/null | head -1 \
    | sed -E 's/^\s*listen\s*=\s*"([^"]+)".*/\1/' \
    || echo "127.0.0.1:11400"
)"
port="${listen##*:}"
echo "$port"
"""
    result = subprocess.run(
        ["bash", "-c", broken, "_", str(config)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == '11400"'
