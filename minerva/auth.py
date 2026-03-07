import keylib
import webbrowser
from urllib.parse import quote

import httpx
from rich.console import Console

from minerva.constants import CALLBACK_ENDPOINT, IS_DOCKER, OAUTH_URL, TOKEN_FILE


def save_token(token: str) -> None:
    keylib.set_password("Minerva-dnt", "na", token)

def load_token() -> str | None:
    return keylib.get_password("Minerva-dnt", "na")


def verify_token(token: str) -> bool:
    try:
        r = httpx.get(url="https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {token}"})
        return r.status_code == 200
    except Exception as e:
        raise Exception(f"Failed to verify token with Discord: {e}") from e


def do_login(server_url: str) -> str:
    console = Console()

    url = OAUTH_URL.format(redirect_uri=quote(f"{server_url}{CALLBACK_ENDPOINT}"))
    console.print("[bold]Opening browser for Discord login...")
    console.print(f"[dim]If it doesn't open: {url}")
    webbrowser.open(url)
    if IS_DOCKER:
        console.print("[dim]You seem to be running in a container which might not be able to open a browser link.")
        console.print("[dim]If the link is not working, see the alternative authentication method in the README.")

    token: str | None = None
    while True:
        token = input("Once you have authorized, enter the given code: ").strip()
        if not token:
            console.print("[red]Token cannot be empty, try again: ")
            continue
        if not verify_token(token):
            console.print("[red]Token is invalid or has expired. Try again: ")
            continue
        break

    save_token(token)
    console.print("[bold green]Login successful!")

    return token
