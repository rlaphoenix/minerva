import webbrowser
from base64 import b64encode
from urllib.parse import quote

import httpx
from rich.console import Console

from minerva.constants import (
    CALLBACK_ENDPOINT,
    IS_DOCKER,
    LEGACY_TOKEN_FILE,
    OAUTH_URL,
    TOKEN_FILE_DIRECTORY,
    USE_KEYRING,
)

try:
    if not USE_KEYRING:
        raise ImportError("Keyring support is intentionally disabled.")
    import keyring
except ImportError:
    keyring = None


def save_token(server_url: str, token: str) -> None:
    if keyring:
        keyring.set_password("Minerva-dnt", server_url, token)
    else:
        TOKEN_FILE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        (TOKEN_FILE_DIRECTORY / b64encode(server_url.encode()).decode()).write_text(token)


def load_token(server: str) -> str | None:
    if LEGACY_TOKEN_FILE.exists():
        token = LEGACY_TOKEN_FILE.read_text().strip()
        if token:
            if not verify_token(token):
                LEGACY_TOKEN_FILE.unlink()
                raise ValueError("Authorization is invalid or has expired. Please run 'minerva login' again.")
            save_token(server, token)
            LEGACY_TOKEN_FILE.unlink()
            return token
    token_file = TOKEN_FILE_DIRECTORY / b64encode(server.encode()).decode()
    if token_file.exists():
        token = token_file.read_text().strip()
        if token:
            if not verify_token(token):
                token_file.unlink()
                raise ValueError("Authorization is invalid or has expired. Please run 'minerva login' again.")
            if keyring:
                save_token(server, token)
            return token
    if keyring:
        token = keyring.get_password("Minerva-dnt", server)
        if token:
            if not verify_token(token):
                keyring.delete_password("Minerva-dnt", server)
                raise ValueError("Authorization is invalid or has expired. Please run 'minerva login' again.")
            return token
    return None


def delete_token(server_url: str) -> None:
    if keyring:
        keyring.delete_password("Minerva-dnt", server_url)
    token_file = TOKEN_FILE_DIRECTORY / b64encode(server_url.encode()).decode()
    if token_file.exists():
        token_file.unlink()
    if LEGACY_TOKEN_FILE.exists():
        LEGACY_TOKEN_FILE.unlink()


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

    save_token(server_url, token)
    console.print("[bold green]Login successful!")

    return token


def do_logout(server_url: str) -> None:
    console = Console()
    delete_token(server_url)
    console.print("[bold green]Logout successful!")
