import shutil
from argparse import ArgumentParser, Namespace
from pathlib import Path
from textwrap import dedent

from PyInstaller.__main__ import run

from minerva import __version__

# -------------------------
# Configuration
# -------------------------

DEFAULT_NAME = "Minerva"
DEFAULT_AUTHOR = "rlaphoenix"
SPEC_FILE = Path(f"{DEFAULT_NAME}.spec")
VERSION_FILE = Path("pyinstaller.version.txt")

ADDITIONAL_DATA: list[tuple[str, str]] = [("minerva/data/sizes.idx", "minerva/data")]
EXTRA_ARGS: list[str] = [
    "-y",
    "--collect-all",
    "rich",
]


# -------------------------
# Argument Parsing
# -------------------------


def parse_args() -> Namespace:
    parser = ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--author", default=DEFAULT_AUTHOR)
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--icon-file", default="icon.ico")
    parser.add_argument("--one-file", action="store_true")
    return parser.parse_args()


# -------------------------
# Utilities
# -------------------------


def clean_build() -> None:
    shutil.rmtree("build", ignore_errors=True)
    shutil.rmtree("dist", ignore_errors=True)
    SPEC_FILE.unlink(missing_ok=True)


def parse_version(version: str) -> tuple[int, int, int, int]:
    major, minor, patch = map(int, version.split("."))
    return major, minor, patch, 0  # Windows version info requires 4 components


def write_version_file(args: Namespace) -> None:
    version_tuple = parse_version(args.version)

    VERSION_FILE.write_text(
        dedent(f"""
        VSVersionInfo(
          ffi=FixedFileInfo(
            filevers={version_tuple},
            prodvers={version_tuple},
            OS=0x40004,
            fileType=0x1,
            subtype=0x0
          ),
          kids=[
            StringFileInfo([
              StringTable(
                '040904b0',
                [
                  StringStruct('CompanyName', '{args.author}'),
                  StringStruct('FileDescription', "Preserving Myrient's legacy, one file at a time."),
                  StringStruct('FileVersion', '{args.version}'),
                  StringStruct('InternalName', '{args.name}'),
                  StringStruct('LegalCopyright', 'Copyright (C) 2026 {args.author}'),
                  StringStruct('OriginalFilename', '{args.name}.exe'),
                  StringStruct('ProductName', '{args.name}'),
                  StringStruct('ProductVersion', '{args.version}'),
                  StringStruct('Comments', '{args.name}')
                ]
              )
            ]),
            VarFileInfo([VarStruct('Translation', [1033, 1200])])
          ]
        )
        """).strip(),
        encoding="utf8",
    )


def build_pyinstaller_args(args: Namespace) -> list[str]:
    cmd = [
        "minerva/__main__.py",
        "-n",
        args.name,
        "--version-file",
        str(VERSION_FILE),
        "-c",  # console mode
        *EXTRA_ARGS,
    ]

    if args.icon_file:
        cmd += ["-i", args.icon_file]

    cmd.append("-F" if args.one_file else "-D")

    for src, dst in ADDITIONAL_DATA:
        cmd += ["--add-data", f"{src}:{dst}"]

    return cmd


# -------------------------
# Main
# -------------------------


def main() -> None:
    args = parse_args()

    clean_build()
    write_version_file(args)

    try:
        run(build_pyinstaller_args(args))
    finally:
        if not args.debug:
            VERSION_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
