"""
Minerva DPN Worker — single-file volunteer download client.
"""

import asyncio
import logging

import click
from rich.logging import RichHandler

from minerva import __version__
from minerva.auth import do_login, load_token
from minerva.console import console
from minerva.constants import (
    CONCURRENCY,
    MAX_JOB_SIZE,
    MIN_JOB_SIZE,
    RETRIES,
    SERVER_URL,
)
from minerva.doctor import doctor_cmd
from minerva.loop import worker_loop
from minerva.version_check import check_for_update


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Minerva Worker — help archive the internet."""
    check_for_update()
    console.print(f"[bold green]Minerva Worker v{__version__}[/bold green]")
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@main.command()
@click.option("--server", default=SERVER_URL, help="Manager server URL")
def login(server: str) -> str:
    """Authenticate with Discord."""
    return do_login(server)


@main.command()
def status() -> None:
    """Show login status."""
    token = load_token()
    console.print("[green]Logged in" if token else "[red]Not logged in")


@main.command()
@click.pass_context
@click.option("--server", default=SERVER_URL, help="Server URL")
@click.option("-c", "--concurrency", default=CONCURRENCY, help="Concurrent jobs")
@click.option("-r", "--retries", default=RETRIES, help="Max amount of attempts for each job")
@click.option("--min-job-size", default=MIN_JOB_SIZE, help="Skip jobs for files smaller than a given size")
@click.option("--max-job-size", default=MAX_JOB_SIZE, help="Skip jobs for files larger than a given size")
def run(
    ctx: click.Context,
    server: str,
    concurrency: int,
    retries: int,
    min_job_size: str,
    max_job_size: str,
) -> None:
    """Start downloading and uploading files."""
    # configure logs
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[
            RichHandler(
                console=console,
                show_path=False,
                markup=True,
                omit_repeated_times=True,
            )
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    log = logging.getLogger(__file__)

    # ensure user is logged-in first
    token = load_token()
    if not token:
        token = ctx.invoke(login, server=server)
    if not token:
        log.error("[red]Could not login, please try again...")
        return

    # start main loop
    asyncio.run(
        worker_loop(
            token,
            server,
            concurrency,
            retries,
            min_job_size,
            max_job_size,
        )
    )


main.add_command(doctor_cmd)

if __name__ == "__main__":
    main()
