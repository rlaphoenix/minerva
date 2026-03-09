# Define various autthentication functions used by the worker

# Import required modules. These must exist or an error is thrown,
import webbrowser  # Used to trigger call to GUI web browser for authentication
from urllib.parse import quote  # Used to encode the URL that'll be used in the OAUTH request

import httpx  # Used to perform HTTPS operations
import keyring  # Used to store/retrieve token
from rich.console import Console  # Fancy-shmancy text for kids

# Imports static constants defined in constants.py
from minerva.constants import CALLBACK_ENDPOINT, IS_DOCKER, KEYRING_SUPPORT, OAUTH_URL, TOKEN_FILE


# Define function for saving authentication token. Takes a single string value as input (token to save)
def save_token(token: str) -> None:
    # If we have keyring support, store it there.
    if KEYRING_SUPPORT:
        # Store token in the users keyring, supported on all major OS's.
        keyring.set_password("Minerva-dnt", "na", token)
    else:
        # Store it on local filesystem
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token)


# Define function to load authentication token
def load_token() -> str | None:
    # Initialize token value
    token = None

    # If we have keyring support, try and load from there first.
    if KEYRING_SUPPORT:
        # Retrieve token from users keyring. If it doesn't exist a None is returned natively.
        # TODO: Migrate file tokens to keyring where supported.
        token = keyring.get_password("Minerva-dnt", "na")

    # No keyring support so load from filesystem
    else:
        # Make sure token file exists
        if TOKEN_FILE.exists():
            # Read token from the file and strip EOL markers.
            # TODO: Handle filesystem errors such as noaccess
            token = TOKEN_FILE.read_text().strip()

            # If we were able to read the token...
            if token:
                # If we can't verify it works...
                if not verify_token(token):
                    # Remove the token file
                    TOKEN_FILE.unlink()

                    # Raise an authentication error
                    raise ValueError("Authorization is invalid or has expired. Please run 'minerva login' again.")
                # we tested ok so existing token vaule is good

    # Return our token value
    return token


# Function to verify the token actually works. Takes a single string value as input (token to test)
def verify_token(token: str) -> bool:
    # Try to perform auth check
    try:
        # Do a simple GET against the Discord API with the provided token
        r = httpx.get(url="https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {token}"})

        # If the status code of the GET was successful (200) return the value.
        return r.status_code == 200
    # Exception thrown if anything went wrong
    except Exception as e:
        # Raise the exception error as returned from the request attempt.
        raise Exception(f"Failed to verify token with Discord: {e}") from e


# Define function to perform initial login. The login perform is to the Minerva projects servers.
# By logging in it is possible to track which users have contributed. Discord is used as the authentication provider.
# This does not give the Minerva project access to your Discord account. The authentication is for a unique client representing the Minerva project and has limited access.
def do_login(server_url: str) -> str:
    # Create an instance of the Console object. Used to display output.
    console = Console()

    # Define the URL for logging in, based on the combination of the passed URL and static constant CALLBACK_ENDPOINT
    url = OAUTH_URL.format(redirect_uri=quote(f"{server_url}{CALLBACK_ENDPOINT}"))

    # User notices blah blah boring
    console.print("[bold]Opening browser for Discord login...")
    console.print(f"[dim]If it doesn't open: {url}")

    # Use the webbrowser module to access the login URL. This will invoke the default browser.
    webbrowser.open(url)

    # If the script is running in a Docker container, warn user they may not see the browser link open.
    if IS_DOCKER:
        console.print("[dim]You seem to be running in a container which might not be able to open a browser link.")
        console.print("[dim]If the link is not working, see the alternative authentication method in the README.")

    # Initialze the variable to hold the token.
    token: str | None = None

    # Loop forever.
    while True:
        # Get the token the user received after authenticating (copy-pasted)
        token = input("Once you have authorized, enter the given code: ").strip()

        # Make sure it isn't empty, i.e. you can't just hit enter.
        if not token:
            console.print("[red]Token cannot be empty, try again: ")
            continue

        # If the token can't be verified to work, make the user try again.
        if not verify_token(token):
            console.print("[red]Token is invalid or has expired. Try again: ")
            continue

        # Token verified successfully, break out of forever loop.
        break

    # Saave the token for future use.
    save_token(token)

    # Notify the user of success
    console.print("[bold green]Login successful!")

    # Return the token to the calling code.
    return token
