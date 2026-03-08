import os
from pathlib import Path

# servers/endpoints
SERVER_URL = os.environ.get("MINERVA_SERVER", "https://firehose.minerva-archive.org")
SERVER_VERSION: int = 4
CALLBACK_ENDPOINT = "/code"
WORKER_ENDPOINT = "/worker"
LEADERBOARD_ENDPOINT = "/api/leaderboard"

# vesioning and identity
USER_AGENT = f"HyperscrapeWorker/v{SERVER_VERSION} (Created by Hackerdude for Minerva)"

# auth
OAUTH_URL = "https://discord.com/oauth2/authorize?client_id=1478862142793977998&response_type=code&redirect_uri={redirect_uri}&scope=identify"
TOKEN_FILE = Path(os.environ.get("MINERVA_TOKEN_FILE", Path.home() / ".minerva-dpn" / "token"))
AUTH_HOST = os.environ.get("MINERVA_AUTH_HOST", "127.0.0.1")
AUTH_PORT = int(os.environ.get("MINERVA_AUTH_PORT", 19283))
KEYRING_SUPPORT = False

# Check if keyring support is availabe by importing the module
try:
    import keyring

    # A successful import means we have support
    KEYRING_SUPPORT = True

    # Appease ruff-check
    keyring.get_keyring()

# Assume it failed and support isn't available.
except Exception:
    pass

# speed tests
SPEED_TEST_URL = "http://ipv4.download.thinkbroadband.com/5MB.zip"
MYRIENT_SPEED_TEST_URL = "https://myrient.erista.me/files/No-Intro/VM%20Labs%20-%20NUON%20%28Digital%29/Atari%202600%20Pac-Man%20%28Unknown%29%20%28Unl%29.zip"

# timeouts and retries
CONNECTIVITY_CHECK_TIMEOUT = 5.0
RETRIES = int(os.environ.get("MINERVA_RETRIES", 5))
RETRY_DELAY = int(os.environ.get("MINERVA_RETRY_DELAY", 5))

# sizes and counts
CONCURRENCY = int(os.environ.get("MINERVA_CONCURRENCY", 2))
MAX_CHUNK_COUNT = 300
SUBCHUNK_SIZE = 996147  # DO NOT CHANGE OR YOUR SCRIPT WILL BREAK!
MIN_JOB_SIZE = os.environ.get("MINERVA_MIN_JOB_SIZE", "")
MAX_JOB_SIZE = os.environ.get("MINERVA_MAX_JOB_SIZE", "")

# ui
HISTORY_LINES = int(os.environ.get("MINERVA_HISTORY_LINES", 5))  # completed jobs shown above active table

# environment
IS_DOCKER = os.environ.get("IS_DOCKER", "").lower() in ("1", "true", "yes")
