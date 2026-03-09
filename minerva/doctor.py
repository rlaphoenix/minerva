import click
import httpx

from minerva.auth import load_token
from minerva.console import console
from minerva.constants import (
    CONNECTIVITY_CHECK_TIMEOUT,
    SERVER_URL,
)
from minerva.version_check import check_for_update


def check_url(name: str, url: str) -> None:
    try:
        with httpx.Client(timeout=CONNECTIVITY_CHECK_TIMEOUT) as client:
            resp = client.get(url)

            # expect any kind of response
            if resp.status_code >= 200 and resp.status_code < 400:
                print_success(name, f"Connected and working (code {resp.status_code})")
            else:
                print_warn(name, f"Connected, but returned HTTP code {resp.status_code}")
    except Exception as e:
        print_error(name, f"Failed to connect - {e}")


def print_success(tag: str, message: str) -> None:
    console.print(f"[green]✅ {tag + ':':<16}[/green] {message}")


def print_error(tag: str, message: str) -> None:
    console.print(f"[red]❌ {tag + ':':<16}[/red] {message}")


def print_warn(tag: str, message: str) -> None:
    console.print(f"[yellow]⚠️ {tag + ':':<16}[/yellow] {message}")


@click.command("doctor")
@click.option("--server", default=SERVER_URL, help="Manager server URL")
def doctor_cmd(server: str) -> None:
    console.print("[bold]Checking your setup...[/bold]")

    token = load_token(server)
    if token:
        print_success("Login Token", "Logged in")
    else:
        print_error("Login Token", "Not set (run `minerva login` first)")

    check_url("Internet", "http://google.com/gen_204")
    check_url("Server", server)

    has_update = check_for_update()
    if not has_update:
        print_success("Script version", "Up to date")
    else:
        print_warn("Script version", "A new version is available")

    # TODO: Add download speed checks

    console.print()
