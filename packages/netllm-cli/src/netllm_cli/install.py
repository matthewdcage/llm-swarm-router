"""Global CLI install and PATH helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from rich.panel import Panel

from netllm_cli.ui import console

UV_LOCAL_BIN = Path.home() / ".local" / "bin"


def find_repo_root(start: Path | None = None) -> Path | None:
    """Return repo root when running from a checkout (has workspace pyproject.toml)."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file():
            continue
        text = pyproject.read_text(encoding="utf-8")
        if 'name = "netllm"' in text and "[tool.uv.workspace]" in text:
            return candidate
    return None


def suggested_cli(subcommand: str = "") -> str:
    """Prefer ./netllm from a repo checkout; fall back to global netllm."""
    root = find_repo_root()
    base = "./netllm" if root else "netllm"
    return f"{base} {subcommand}".strip() if subcommand else base


def listen_is_loopback(listen: str) -> bool:
    if listen.startswith("http"):
        from urllib.parse import urlparse

        host = urlparse(listen).hostname or ""
    else:
        host = listen.split(":")[0]
    return host in ("127.0.0.1", "localhost", "::1", "")


def resolved_netllm() -> Path | None:
    """Path to the `netllm` executable that would run if invoked now."""
    found = shutil.which("netllm")
    return Path(found).resolve() if found else None


def global_cli_on_path() -> bool:
    """True when `netllm` on PATH is the uv-tool install in ~/.local/bin."""
    resolved = resolved_netllm()
    if resolved is None:
        return False
    try:
        return resolved == global_netllm_binary().resolve()
    except OSError:
        return False


def netllm_on_path() -> bool:
    return resolved_netllm() is not None


def global_netllm_binary() -> Path:
    return UV_LOCAL_BIN / "netllm"


def global_netllm_installed() -> bool:
    return global_netllm_binary().is_file()


def local_bin_on_path() -> bool:
    local_bin = str(UV_LOCAL_BIN)
    return local_bin in os.environ.get("PATH", "").split(":")


def shell_profile_has_local_bin() -> bool:
    """True when a shell startup file already appends ~/.local/bin."""
    needle = str(UV_LOCAL_BIN)
    for name in (".zshrc", ".zprofile", ".bash_profile", ".bashrc"):
        path = Path.home() / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if needle in text:
            return True
    return False


def path_export_line() -> str:
    return f'export PATH="{UV_LOCAL_BIN}:$PATH"'


def _run_update_shell() -> None:
    """Register ~/.local/bin in shell profile; ignore 'already up-to-date' errors."""
    result = subprocess.run(
        ["uv", "tool", "update-shell"],
        capture_output=True,
        text=True,
    )
    out = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if result.returncode == 0:
        return
    lowered = out.lower()
    if "already up-to-date" in lowered or "already in path" in lowered:
        return
    if out:
        console.print(f"[yellow]Shell profile note:[/] {out}")


def _run_uv_tool_install(repo_root: Path) -> None:
    """Install global netllm; succeed if ~/.local/bin/netllm exists afterward."""
    result = subprocess.run(
        ["uv", "tool", "install", "--editable", str(repo_root), "--reinstall"],
        capture_output=True,
        text=True,
    )
    out = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if global_netllm_installed():
        return
    if out:
        console.print(out)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, out)


def install_global_cli(repo_root: Path | None = None) -> None:
    """Install netllm via uv tool and register ~/.local/bin in the shell profile."""
    root = repo_root or find_repo_root()
    if root is None:
        raise RuntimeError(
            "Cannot find netllm repo root — run from the clone or pass --repo"
        )
    if shutil.which("uv") is None:
        raise RuntimeError(
            "uv is not on PATH — install uv first: https://docs.astral.sh/uv/"
        )

    _run_uv_tool_install(root)
    _run_update_shell()


def print_path_notice(*, installed: bool) -> None:
    """Explain PATH for this terminal vs new terminals."""
    if global_cli_on_path():
        console.print(
            "[green]Global CLI ready:[/] [cyan]netllm[/] → "
            f"{global_netllm_binary()}"
        )
        return

    if global_netllm_installed() and netllm_on_path() and not global_cli_on_path():
        console.print(
            "[yellow]Note:[/] [cyan]netllm[/] on PATH is from [cyan]uv run[/] "
            f"({resolved_netllm()}), not the global install."
        )

    lines: list[str] = []
    if installed or global_netllm_installed():
        lines.append(
            "[green]Installed[/] [cyan]~/.local/bin/netllm[/] successfully."
        )

    if shell_profile_has_local_bin():
        lines.append(
            "Your shell profile already lists [cyan]~/.local/bin[/] — "
            "this terminal just has not loaded it yet."
        )
    else:
        lines.append("Shell profile updated for [bold]new[/] terminal tabs.")

    lines.append("")
    lines.append("[bold]Use netllm in this terminal[/] — pick one:")
    lines.append(f"  [cyan]{path_export_line()}[/]")
    lines.append("  [cyan]source ~/.zshrc[/]")
    lines.append("")
    lines.append(
        "[dim]Or use [cyan]./netllm[/] from the repo — no PATH changes needed.[/]"
    )
    console.print(Panel("\n".join(lines), title="Global CLI", border_style="green"))


def ensure_global_cli(repo_root: Path | None = None) -> bool:
    """
    Install global CLI + update shell profile. Returns True if binary exists.
    """
    try:
        install_global_cli(repo_root)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]Global CLI install failed[/] (exit {exc.returncode})")
        return global_netllm_installed()
    except RuntimeError as exc:
        console.print(f"[yellow]Skipping global CLI:[/] {exc}")
        return False
    return global_netllm_installed()
