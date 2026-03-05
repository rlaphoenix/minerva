<p align="center">
  <a href="https://github.com/minerva-archive/worker">Minerva Worker</a>
  <br/>
  <sup><em>Preserving Myrient's legacy, one file at a time.</em></sup>
</p>

<p align="center">
  <a href="https://github.com/minerva-archive/worker/blob/master/LICENSE">
    <img src="https://img.shields.io/:license-CC%201.0-blue.svg" alt="License">
  </a>
  <a href="https://pypi.org/project/minerva-worker/">
    <img src="https://img.shields.io/badge/python-3.10%2B-informational" alt="Python version">
  </a>
  <a href="https://github.com/astral-sh/uv">
    <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Onyx-Nostalgia/uv/refs/heads/fix/logo-badge/assets/badge/v0.json" alt="Manager: uv">
  </a>
  <a href="https://github.com/astral-sh/ruff">
    <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Linter: Ruff">
  </a>
  <a href="https://github.com/minerva-archive/worker/actions/workflows/ci.yml">
    <img src="https://github.com/minerva-archive/worker/actions/workflows/ci.yml/badge.svg" alt="Build status">
  </a>
</p>

* * *

Myrient is shutting down. Minerva is a volunteer-driven effort to archive its entire collection before it goes offline.
Run a script, share your bandwidth, help preserve the archive.

## Installation

Download and install the Python script from PIP/PyPI:

```shell
$ pip install minerva-worker
```

> [!NOTE]
If pip gives you a warning about a path not being in your PATH environment variable then promptly add that path then
close all open command prompt Windows, or running `minerva` won't work as it will not be recognized as a program.

You now have the `minerva` package installed - Voilà 🎉!  
Get started by running `minerva` in your Terminal or Windows Run.  
For configuration options, see help and options by running `minerva --help`.

*Alternatively, a Windows EXE is available on the [Releases] page, simply run it to begin!*

  [Releases]: <https://github.com/minerva-archive/worker/releases>

## Usage

It's very easy to use. Simply run the minerva.exe file, or run `minerva` in your Terminal/Command Prompt.

You can configure the worker settings by running `minerva run --help` to see what configuration options
you can change. If you have a great computer and network, it's recommended to bump up the -c and -b
options.

> [!TIP]
It's recommended to keep -c smaller or the same as -b.

## How it works

The worker script asks the minerva server for jobs to download. The server gives active workers random
missing needed file URLs to download. When the worker is given a job, it temporarily downloads the file
and uploads it to the minerva file servers. Once the file is downloaded, it is deleted from your machine.

> [!TIP]
If you also want a copy of the files, use the `--keep-files` setting.

Jobs are given exclusively to each worker, no two workers download the same file at the same time. However,
to verify that uploads aren't corrupted, each job gets given (eventually) to a second worker. Both uploads
are then confirmed and if both workers give back the same file to the minerva file server, then the job is
marked as complete and verified.

> [!NOTE]
You may see 409 Conflict errors on upload, this happens when either you or another worker had mismatching
files uploaded. Just ignore these error's and let the worker continue. If you suspect every file has this
issue, please verify your network connection is stable and verify your downloads arent corrupted.

## Discord Authentication

When using the minerva worker, you are prompted upon startup to login and authorize with Discord. This is
to authenticate unique users on the minerva server, to know who jobs are given to, and to use your username
and avatar in the worker dashboard leaderboards.

This does not give Minerva, this script, or anyone else access to your account, or any permissions.

## Docker

You can run the Minerva Worker inside a headless Docker container.
The following steps assume some knowledge on git/docker.

To change the settings of the worker, you can set the following environment variables:

- `--server`: `MINERVA_SERVER`
- `-c/--concurrency`: `MINERVA_CONCURRENCY`

There are more advanced environment variables available, you can find them listed in
[constants.py](/minerva/constants.py).

### 1. Download a copy of the repository

- Clone the Git Repository: `git clone https://github.com/minerva-archive/worker`  
- Enter it: `cd minerva`

### 2. Get an Authorization Token

There's two ways to go about this,

- either run the normal python script to authenticate with it,
- or, get and save the token to a specific location manually.

To get the token manually, go to <https://api.minerva-archive.org/auth/discord/login> and you will get a
token value once you authorize with Discord. Copy that token value and save it to `~/.minerva-dpn/token`
on Linux/macOS, or save it to `%USERPROFILE%/.minerva-dpn/token` on Windows.

The token file must stay there at all times for the Docker container to have it. The location where the
token needs to be can be changed, just make sure you change the volume location and environment variable
in the docker-compose config.

### 3. Start the container

Build the Docker image and start the container with `docker compose up -d` to run it in the background.
You can later stop the container with `docker compose down`.

> [!TIP]
If you prefer to run it in the foreground (attached to your terminal), simply use `docker compose up`.

### Terminal Interactivity

These options allow you to interact with the container directly through the terminal.

The configuration is controlled via the `stdin_open` and `tty` options in the [`docker-compose.yml`](./docker-compose.yml)

```yml
stdin_open: true  # Keeps STDIN open even if not attached
tty: true         # Allocates a pseudo-TTY for the container
```

If you do not need terminal interactivity, you can comment out or remove these lines. This is useful for running the container in the background without manual input.

When these are enabled, you may prefer to use `docker attach <container_name>` instead of `docker logs <container_name>` to see real-time output without duplicated lines, and to provide input.

> [!TIP]
To safely detach from an attached container without stopping it, use the key sequence `CTRL + P` then `CTRL + Q`.

## Development

1. Install [uv]
2. `uv sync --all-extras --all-groups`
3. `.venv\Scripts\activate` (or `source .venv/bin/activate` on macOS and Linux)
4. `uv tool install pre-commit --with pre-commit-uv --force-reinstall`
5. `pre-commit install`

Now feel free to work on the project however you like, all code will be checked before committing.

  [uv]: <https://docs.astral.sh/uv>

## Licensing

This software is licensed under the terms of [CC0 1.0 Universal](LICENSE).
You can find a copy of the license in the LICENSE file in the root folder

* * *

© minerva-archive 2026
