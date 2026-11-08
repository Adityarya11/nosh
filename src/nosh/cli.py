"""Command-line entry point: nosh build | chat | ui | eval."""

import argparse


def _chat(mode: str) -> int:
    from nosh.chat import turn
    from nosh.llm import LLMUnavailable, check_ready
    from nosh.session import SessionState

    try:
        check_ready()
    except LLMUnavailable as exc:
        print(f"Chat needs an LLM: {exc}")
        return 1
    session = SessionState(mode=mode)
    print("nosh chat (type 'quit' to leave)")
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if text.lower() in {"quit", "exit"}:
            return 0
        if text:
            try:
                print("\n" + turn(text, session))
            except Exception as exc:  # degrade to a message, never a stack trace
                print(f"\nSomething went wrong ({type(exc).__name__}): {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="nosh")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="build data/nosh.duckdb from recipes_raw/")
    chat = sub.add_parser("chat", help="interactive chat in the terminal")
    chat.add_argument(
        "--mode", choices=["auto", "search", "match", "substitute"], default="auto"
    )
    args = parser.parse_args()
    if args.command == "build":
        from nosh.data.build import build

        build()
    elif args.command == "chat":
        raise SystemExit(_chat(args.mode))


if __name__ == "__main__":
    main()
