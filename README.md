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

## How it works

The worker script asks the minerva server for jobs to download. The server gives active workers random
missing needed file URLs to download. When the worker is given a job, it temporarily downloads the file
and uploads it to the minerva file servers. Once the file is downloaded, it is deleted from your machine.

Jobs are given exclusively to each worker, no two workers download the same file at the same time. However,
to verify that uploads aren't corrupted, each job gets given (eventually) to a second worker. Both uploads
are then confirmed and if both workers give back the same file to the minerva file server, then the job is
marked as complete and verified.

## Discord Authentication

When using the minerva worker, you are prompted upon startup to login and authorize with Discord. This is
to authenticate unique users on the minerva server, to know who jobs are given to, and to use your username
and avatar in the worker dashboard leaderboards.

This does not give Minerva, this script, or anyone else access to your account, or any permissions.

## Docker

You can run the Minerva Worker inside a Docker container.

1. Download `docker-compose.yml` from the repository:  
   `curl -L https://raw.githubusercontent.com/minerva-archive/worker/main/docker-compose.yml -o docker-compose.yml`
2. Before running the worker you must authenticate once:
   `docker compose run --rm -it login`  
   Once you authenticate, a token file is saved to `~/.minerva-dpn/token` on your host machine
3. Start the worker to begin the archiving process: `docker compose up -d`  
   This runs the worker in the background, to stop it run `docker compose down`  
   If you want to see it running, instead run `docker compose up`

> [!NOTE]
The container stores your login token in `~/.minerva-dpn/token` on the host.
Do not delete this file, it will be re-used every time you run `docker compose up`.

### Building Locally

If you prefer to run the latest code and/or prefer to build and run the code locally,
clone/download the repository and add `--build` to all uses of `docker compose` above.

```bash
git clone https://github.com/minerva-archive/worker  # download the repo
cd worker  # enter the repo
docker compose run --rm -it --build login  # login
docker compose up -d --build  # build and run in the background
```

### Configuration

Worker settings can be changed via environment variables:

- `--server` → `MINERVA_SERVER`: Change the Server URL
- `-c/--concurrency` → `MINERVA_CONCURRENCY`: Set the amount of jobs to work on
- `-r/--retries` → `MINERVA_RETRIES`: How many retry attempts per job
- `--min-job-size` → `MINERVA_MIN_JOB_SIZE`: Skip jobs if they are too small (e.g., `1MB`)
- `--max-job-size` → `MINERVA_MAX_JOB_SIZE`: Skip jobs if they are too big (e.g., `40MB`)

On Linux systems, these would be set like so:

```bash
export MINERVA_CONCURRENCY=10
export MINERVA_MIN_JOB_SIZE=1MB
docker compose up
```

More advanced options are listed in [`constants.py`](/minerva/constants.py).

### Attaching to a Headless Container

If you ran the worker with `docker compose up -d` and now want to take a look at it,
you can attach to the docker container with `docker attach <container_name>`. This
container name could be anything, see `docker ps`.

To safely detach without stopping the container, press `CTRL+P` followed by `CTRL+Q`.
To end the worker container, press `CTRL+C`.

## Troubleshooting

Before continuing, make sure you are using the worker installed using the instructions above. Delete any `minerva.py` file you may have as your terminal might run that when calling `minerva` instead of the pip package installed as instructed above. Also double check that your Python version is `3.10.0` or newer with `python --version`. If you use `python3`, try `python` and see if one or the other is newer.

1. `This environment is externally managed`

You use a Linux system that prevents `pip` from modifying the Python environment to avoid breaking packages used by the OS.
Instead, install `pipx`, for example with `sudo apt install pipx`, then run `pipx ensurepath`.
Now install the worker by calling `pipx install minerva-worker`.

2. Other `pip`-related installation issues

Commands like `pip install minerva-worker` must be run in a Terminal/Command Prompt, not typed into a Python script, Python shell, Notepad, Windows Run, or any other program. We highly recommend using `Microsoft Terminal` on both Windows or Linux as it plays really well with the UI.

3. `minerva` was not found

This happens because Python installs command scripts into a Scripts directory that may not be in your System PATH Environment Variable.

It's typical location on Windows would be in:
`C:\Users\<username>\AppData\Local\Programs\Python\PythonXXX\Scripts` where `<username>` is your Windows Username and `PythonXXX` is your Python version, e.g. "Python314".

Add this location to the `PATH` environment variable listed under `User variables` in the "Environment Variables" settings window. In your start menu search "Environment variables", open it, and click "Edit environment variables". If you need assistance I recommend searching for a guide online.

4. The interface is laggy, buggy, or similar.

On Windows, I highly recommend using [Windows Terminal](https://github.com/microsoft/terminal) with the latest version of [PowerShell](https://learn.microsoft.com/en-us/powershell/scripting/install/install-powershell-on-windows?view=powershell-7.5). If you're running the worker in a shell or command prompt that has a bright blue background, your using an old version of PowerShell and are going to have a bad time.

On Linux or macOS, I have no preferences or recommendation to share, but I recommend looking around. If you are running the worker in a less typical environment or device, like Docker or cloud-based terminals, you may unfortunately be stuck with having a slow or glitchy UI.

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
