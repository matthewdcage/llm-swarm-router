"""User-facing CLI for netllm.

Typer wiring only. Every command body lives in `netllm_cli.commands.*`; this
module owns the `app` object, the version literals, and the registration
order (which is what `netllm --help` prints).
"""

from __future__ import annotations

import typer
from netllm_core.version import get_version

from netllm_cli.commands import (
    cloud,
    config_io,
    connect,
    diagnose,
    init_install,
    join_swarm,
    observe,
    serve_lifecycle,
    sources,
)
from netllm_cli.ui import console

__version__ = get_version()

app = typer.Typer(
    name="netllm",
    help="Network LLM router — discover, route, and load-balance local inference.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"netllm [cyan]{__version__}[/]")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Network LLM router CLI."""


# Registration order below is the order `netllm --help` lists commands in; it
# reproduces the top-to-bottom order of the pre-split single-file main.py.
app.command()(init_install.init)
app.command()(init_install.install)
app.command()(join_swarm.discover)
app.command()(join_swarm.join)
app.command("swarm-token")(join_swarm.swarm_token)
app.command()(observe.models)
app.command()(observe.peers)
app.command("env")(observe.env_shell)
app.command()(serve_lifecycle.serve)
app.command()(observe.drain)
app.command()(observe.status)
app.command()(diagnose.test)
app.command("connect")(connect.connect_tool)
app.command("gateway")(diagnose.gateway_enable)
app.command()(diagnose.doctor)
app.add_typer(config_io.config_app, name="config")
app.add_typer(cloud.cloud_app, name="cloud")
app.add_typer(sources.sources_app, name="sources")
app.command()(serve_lifecycle.start)
app.command()(serve_lifecycle.stop)
app.command()(serve_lifecycle.restart)
app.command()(serve_lifecycle.config_edit)


if __name__ == "__main__":
    app()
