"""Command-line entry point: nosh build | chat | ui | eval."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="nosh")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="build data/nosh.duckdb from recipes_raw/")
    args = parser.parse_args()
    if args.command == "build":
        from nosh.data.build import build

        build()


if __name__ == "__main__":
    main()
