"""Operator CLI for the single synthetic user-zero enrollment."""

from __future__ import annotations

import argparse
import getpass

from .inbound import InboundStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage B user-zero enrollment")
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("begin", "confirm", "set-agent-recipient"):
        command = sub.add_parser(action)
        command.add_argument("--email", required=True)
        if action == "confirm":
            command.add_argument("--token", required=True)
        if action == "set-agent-recipient":
            command.add_argument("--agent-email", required=True)
    args = parser.parse_args()
    store = InboundStore(args.db)
    try:
        if args.action == "begin":
            user_id, token = store.begin_signup(args.email)
            print(f"user_id={user_id}")
            print(f"confirmation_token={token}")
            print("Deliver this token to the named synthetic user through the controlled confirmation step.")
        elif args.action == "confirm":
            print(f"confirmed_user_id={store.confirm_signup(args.email, args.token)}")
        else:
            token = getpass.getpass("Existing enrollment token: ")
            recipient = store.configure_agent_recipient(
                args.email, token, args.agent_email
            )
            print(f"agent_recipient={recipient}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
