"""Read-only check against the real Trello API using your .env credentials.

Nothing here writes to a board. It confirms the credentials work and prints
what the analysis tools see, which is the quickest way to tell whether a
disappointing result is a bug or just a quiet board.

Run: .venv/Scripts/python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json

from trellis.config import MissingCredentials
from trellis.server import (
    analyze_board_health,
    detect_bottlenecks,
    generate_standup_report,
    list_board_lists,
    list_boards,
)
from trellis.trello import TrelloError


def show(title: str, payload: object, limit: int = 1400) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
    text = json.dumps(payload, indent=2, default=str)
    print(text[:limit] + (f"\n... [{len(text) - limit} more chars]" if len(text) > limit else ""))


async def main() -> int:
    try:
        boards = await list_boards()
    except MissingCredentials as exc:
        print(f"No credentials: {exc}")
        return 1
    except TrelloError as exc:
        print(f"Trello rejected the request: {exc}")
        return 1

    if "error" in boards:
        print(f"Could not list boards: {boards['error']}")
        return 1

    show(f"BOARDS ({boards['count']})", boards)
    if not boards["count"]:
        print("\nNo boards on this account, so there is nothing further to test.")
        return 0

    # Prefer the most recently active board: it gives the analysis tools something
    # to actually chew on rather than an empty template board.
    target = max(boards["boards"], key=lambda b: b.get("last_activity") or "")
    name = target["name"]
    print(f"\n\nUsing most recently active board: {name!r} ({target['id']})")

    show(f"LISTS on {name}", await list_board_lists(target["id"]))
    show(f"HEALTH of {name}", await analyze_board_health(target["id"]))
    show(f"BOTTLENECKS on {name}", await detect_bottlenecks(target["id"]))
    show(f"STANDUP for {name} (14d)", await generate_standup_report(target["id"], 14))

    print("\n\nSmoke test finished. Nothing was written to any board.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
