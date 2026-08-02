"""Command implementations for the netllm CLI.

Each module owns one command family; `netllm_cli.main` imports them and
registers the callables on the Typer app. Patch targets therefore live in
the owning module namespace (e.g. `netllm_cli.commands.diagnose.mdns_available`),
not in `netllm_cli.main`.
"""

from __future__ import annotations
