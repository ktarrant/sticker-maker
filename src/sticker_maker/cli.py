"""Command line entry point for Sticker Maker."""

from __future__ import annotations

import argparse

from sticker_maker import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sticker-maker",
        description="Turn overhead photos into Cricut-ready sticker cut paths.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args()
    parser.print_help()


if __name__ == "__main__":
    main()
