"""
Minerva DPN Worker — single-file volunteer download client.

Optional (faster downloads):
    Install aria2c: https://aria2.github.io/

Usage:
    minerva login                    # Authenticate with Discord
    minerva run                      # Start downloading
    minerva run -c 8 -b 24           # 8 concurrent, fetch 24 per batch
    minerva run --server http://...  # Custom server URL
"""

import asyncio
from pathlib import Path

import click
from humanfriendly import parse_size

from minerva import __version__
from minerva.auth import do_login, load_token
from minerva.console import console
from minerva.constants import (
    ARIA2C_CONNECTIONS,
    ARIA2C_PRE_ALLOCATION,
    BATCH_SIZE,
    CONCURRENCY,
    KEEP_FILES,
    MAX_DOWNLOAD_RETRIES,
    MAX_UPLOAD_RETRIES,
    SERVER_URL,
    SIZE_IDX_FILE,
    SKIP_CACHE,
    TEMP_DIR,
    UPLOAD_SERVER_URL,
)
from minerva.doctor import doctor_cmd
from minerva.loop import worker_loop
from minerva.size_map import init_index
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
@click.option("--server", default=SERVER_URL, help="Manager server URL")
@click.option("--upload-server", default=UPLOAD_SERVER_URL, help="Upload API URL")
@click.option("-c", "--concurrency", default=CONCURRENCY, help="Concurrent downloads")
@click.option("-b", "--batch-size", default=BATCH_SIZE, help="Max files to fetch per API call")
@click.option("-d", "--dl-retries", default=MAX_DOWNLOAD_RETRIES, help="Max amount of attempts to download each job")
@click.option("-u", "--ul-retries", default=MAX_UPLOAD_RETRIES, help="Max amount of attempts to upload each job")
@click.option("-m", "--max-cache-size", default="", help="Max amount of storage to use at any given moment")
@click.option("-a", "--aria2c-connections", default=ARIA2C_CONNECTIONS, help="aria2c connections per file")
@click.option(
    "-p",
    "--pre-allocation",
    default=ARIA2C_PRE_ALLOCATION,
    help="Pre-allocation method when using aria2c (prealloc, falloc, none)",
)
@click.option("--temp-dir", default=str(TEMP_DIR), help="Temp download dir")
@click.option("--min-job-size", default="", help="Skip jobs for files smaller than a given size")
@click.option("--max-job-size", default="", help="Skip jobs for files larger than a given size")
@click.option("--keep-files", is_flag=True, default=KEEP_FILES, help="Keep downloaded files after upload")
@click.option(
    "--skip-cache", is_flag=True, default=SKIP_CACHE, help="Skip the cache (only recommended when server is down)"
)
def run(
    ctx: click.Context,
    server: str,
    upload_server: str,
    concurrency: int,
    batch_size: int,
    dl_retries: int,
    ul_retries: int,
    max_cache_size: str,
    aria2c_connections: int,
    pre_allocation: str,
    temp_dir: str,
    min_job_size: str,
    max_job_size: str,
    keep_files: bool,
    skip_cache: bool,
) -> None:
    """Start downloading and uploading files."""
    # ensure user is logged-in first
    token = load_token()
    if not token:
        token = ctx.invoke(login, server=server)
    if not token:
        console.print("[red]Could not login, please try again...")
        return

    # initialize the file size index
    init_index(SIZE_IDX_FILE)

    # make sure max cache size isn't set too small or it will constantly ask for jobs
    if max_cache_size and parse_size(max_cache_size) < parse_size("10GiB"):
        console.print("[red]The --max-cache-size cannot be smaller than 10GiB[/red]")

    # start main loop
    asyncio.run(
        worker_loop(
            server,
            upload_server,
            token,
            Path(temp_dir),
            concurrency,
            batch_size,
            dl_retries,
            ul_retries,
            max_cache_size,
            aria2c_connections,
            pre_allocation,
            min_job_size,
            max_job_size,
            keep_files,
            skip_cache,
        )
    )


main.add_command(doctor_cmd)

if __name__ == "__main__":
    main()
