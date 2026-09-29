"""Operator CLI for the single synthetic user-zero enrollment."""

from __future__ import annotations

import argparse

from .inbound import InboundStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage B user-zero enrollment")
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("begin", "confirm"):
        command = sub.add_parser(action)
        command.add_argument("--email", required=True)
        if action == "confirm":
            command.add_argument("--token", required=True)
    args = parser.parse_args()
    store = InboundStore(args.db)
    try:
        if args.action == "begin":
            user_id, token = store.begin_signup(args.email)
            print(f"user_id={user_id}")
            print(f"confirmation_token={token}")
            print("Deliver this token to the named synthetic user through the controlled confirmation step.")
        else:
            print(f"confirmed_user_id={store.confirm_signup(args.email, args.token)}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
