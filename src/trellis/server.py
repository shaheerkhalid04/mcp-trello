"""Trellis: an MCP server that reads a Trello workspace and explains how work is flowing.

Tools are split into two layers:

  * Data tools mirror the Trello REST API one call at a time, trimmed to the
    fields that are useful in a model's context window.
  * Analysis tools compose several calls and return computed metrics. They do
    not write prose. The client model reads the numbers and does the talking,
    which keeps the server honest and free of a second LLM dependency.

Run it over stdio (the default) or streamable HTTP by setting MCP_TRANSPORT.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import analysis
from .config import MissingCredentials
from .trello import TrelloClient, TrelloError

server = MCPServer(
    name="trellis",
    title="Trellis: Trello board analyst",
    version="0.1.0",
    instructions=(
        "Trellis exposes a Trello workspace. Start with list_boards to find a board id, "
        "then use analyze_board_health or generate_standup_report for a read on the board, "
        "or the list_/get_ tools to pull specific records. Analysis tools return metrics, "
        "not conclusions: read the numbers and say what they mean, including when they are "
        "too thin to support a claim. Tools that change Trello data (create_card, move_card, "
        "update_card, add_comment) act on the real board immediately, so confirm intent "
        "before calling them."
    ),
)

_client: TrelloClient | None = None


def get_client() -> TrelloClient:
    """Build the API client on first use so the tool list works without credentials."""
    global _client
    if _client is None:
        _client = TrelloClient()
    return _client


async def _call(coro_factory: Any) -> Any:
    """Run a Trello call, converting our two known failures into readable text.

    Raising a plain string back to the client beats a stack trace: the model can
    relay a missing-credential or expired-token message straight to the user.
    """
    try:
        return await coro_factory
    except (TrelloError, MissingCredentials) as exc:
        return {"error": str(exc)}


async def _resolve_board(board: str) -> str | dict[str, Any]:
    """Accept a board id, short link, or exact-ish board name.

    Board names are what a person actually says out loud, so resolving them here
    saves the model a guess-and-retry round trip.
    """
    if len(board) in (8, 24) and all(c.isalnum() for c in board):
        return board

    boards = await get_client().my_boards()
    if isinstance(boards, dict):
        return boards

    lowered = board.lower()
    exact = [b for b in boards if (b.get("name") or "").lower() == lowered]
    partial = [b for b in boards if lowered in (b.get("name") or "").lower()]
    matches = exact or partial

    if not matches:
        names = ", ".join(sorted((b.get("name") or "?") for b in boards)[:20]) or "none"
        return {"error": f"No board matched {board!r}. Boards available: {names}"}
    if len(matches) > 1 and not exact:
        names = ", ".join((b.get("name") or "?") for b in matches[:10])
        return {"error": f"{board!r} matched several boards: {names}. Use the board id."}
    return matches[0]["id"]


# --- data tools ------------------------------------------------------------


@server.tool()
async def list_boards(include_closed: bool = False) -> dict[str, Any]:
    """List every Trello board the authenticated account can open.

    Returns board ids, names and URLs. This is the entry point for every other
    tool, which all need a board id or name.
    """
    boards = await _call(get_client().my_boards(include_closed))
    if isinstance(boards, dict):
        return boards
    return {
        "count": len(boards),
        "boards": [
            {
                "id": b.get("id"),
                "name": b.get("name"),
                "url": b.get("shortUrl") or b.get("url"),
                "closed": b.get("closed"),
                "last_activity": b.get("dateLastActivity"),
            }
            for b in boards
        ],
    }


@server.tool()
async def get_board_info(board: str) -> dict[str, Any]:
    """Get metadata for one board: name, description, URL, visibility, last activity.

    `board` accepts a board id or a board name.
    """
    board_id = await _resolve_board(board)
    if isinstance(board_id, dict):
        return board_id

    data = await _call(get_client().board(board_id))
    if "error" in (data or {}):
        return data
    members = await _call(get_client().board_members(board_id))
    prefs = data.get("prefs") or {}
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "description": data.get("desc"),
        "url": data.get("shortUrl") or data.get("url"),
        "closed": data.get("closed"),
        "visibility": prefs.get("permissionLevel"),
        "last_activity": data.get("dateLastActivity"),
        "members": members if isinstance(members, list) else [],
    }


@server.tool()
async def list_board_lists(board: str) -> dict[str, Any]:
    """List the open lists (columns) on a board, in board order, with card counts."""
    board_id = await _resolve_board(board)
    if isinstance(board_id, dict):
        return board_id

    lists = await _call(get_client().board_lists(board_id))
    if isinstance(lists, dict):
        return lists
    cards = await _call(get_client().board_cards(board_id))
    counts: dict[str, int] = {}
    if isinstance(cards, list):
        for card in cards:
            counts[card.get("idList")] = counts.get(card.get("idList"), 0) + 1

    return {
        "board_id": board_id,
        "lists": [
            {
                "id": lst.get("id"),
                "name": lst.get("name"),
                "stage": analysis.classify_list(lst.get("name", "")),
                "card_count": counts.get(lst.get("id"), 0),
            }
            for lst in lists
        ],
    }


@server.tool()
async def list_cards(
    board: str,
    list_name: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List open cards on a board, optionally filtered to one list by name.

    Each card is trimmed to a triage digest: labels, assignee count, due date,
    overdue flag, days idle, and checklist progress.
    """
    board_id = await _resolve_board(board)
    if isinstance(board_id, dict):
        return board_id

    lists = await _call(get_client().board_lists(board_id))
    if isinstance(lists, dict):
        return lists
    cards = await _call(get_client().board_cards(board_id))
    if isinstance(cards, dict):
        return cards

    names = {lst["id"]: lst.get("name", "(unnamed)") for lst in lists}
    if list_name:
        wanted = {
            lid for lid, name in names.items() if list_name.lower() in (name or "").lower()
        }
        if not wanted:
            return {
                "error": f"No list on this board matched {list_name!r}. "
                f"Lists: {', '.join(names.values())}"
            }
        cards = [c for c in cards if c.get("idList") in wanted]

    digests = [analysis.card_digest(c) for c in cards]
    return {
        "board_id": board_id,
        "filtered_to_list": list_name,
        "total_matching": len(digests),
        "returned": min(len(digests), limit),
        "cards": [
            {**d, "list_name": names.get(d["list_id"], "(unknown)")} for d in digests[:limit]
        ],
    }


@server.tool()
async def get_card(card_id: str) -> dict[str, Any]:
    """Get one card in full: description, labels, due date, checklists and recent comments."""
    card = await _call(get_client().card(card_id))
    if "error" in (card or {}):
        return card

    comments = await _call(get_client().card_comments(card_id))
    checklists = [
        {
            "name": cl.get("name"),
            "items": [
                {"name": item.get("name"), "state": item.get("state")}
                for item in cl.get("checkItems") or []
            ],
        }
        for cl in card.get("checklists") or []
    ]
    return {
        **analysis.card_digest(card),
        "description": card.get("desc"),
        "checklists": checklists,
        "comments": [
            {
                "author": ((c.get("memberCreator") or {}).get("fullName")),
                "date": c.get("date"),
                "text": ((c.get("data") or {}).get("text") or "")[:1000],
            }
            for c in (comments if isinstance(comments, list) else [])
        ],
    }


@server.tool()
async def search_trello(query: str, limit: int = 20) -> dict[str, Any]:
    """Search cards and boards across the whole account using Trello's own search.

    Supports Trello search operators such as `label:bug`, `due:week`, `is:open`
    and `@username`.
    """
    results = await _call(get_client().search(query, limit))
    if "error" in (results or {}):
        return results
    return {
        "query": query,
        "cards": [analysis.card_digest(c) for c in results.get("cards") or []],
        "boards": [
            {"id": b.get("id"), "name": b.get("name"), "url": b.get("shortUrl")}
            for b in results.get("boards") or []
        ],
    }


# --- analysis tools --------------------------------------------------------


@server.tool()
async def analyze_board_health(board: str, stale_after_days: int = 14) -> dict[str, Any]:
    """Assess a board: per-list load, overdue cards, unassigned work and stalled cards.

    Returns counts and the specific offending cards, not a verdict. Read the
    numbers and say what they imply, including when the board is simply small.
    """
    board_id = await _resolve_board(board)
    if isinstance(board_id, dict):
        return board_id

    lists = await _call(get_client().board_lists(board_id))
    if isinstance(lists, dict):
        return lists
    cards = await _call(get_client().board_cards(board_id))
    if isinstance(cards, dict):
        return cards

    report = analysis.board_health(lists, cards, stale_after_days)
    return {"board_id": board_id, **report}


@server.tool()
async def detect_bottlenecks(board: str) -> dict[str, Any]:
    """Rank the non-done lists by how much work is piling up and ageing inside them.

    The pressure score is a crude volume-plus-age heuristic and is returned with
    its own formula so it can be argued with rather than trusted blindly.
    """
    board_id = await _resolve_board(board)
    if isinstance(board_id, dict):
        return board_id

    lists = await _call(get_client().board_lists(board_id))
    if isinstance(lists, dict):
        return lists
    cards = await _call(get_client().board_cards(board_id))
    if isinstance(cards, dict):
        return cards

    return {"board_id": board_id, **analysis.bottlenecks(lists, cards)}


@server.tool()
async def generate_standup_report(board: str, window_days: int = 7) -> dict[str, Any]:
    """Summarise what actually happened on a board recently.

    Combines card movements, new cards and comments from the activity feed with
    the board's current overdue and stalled work, which is enough material for a
    standup update or a weekly status note.
    """
    board_id = await _resolve_board(board)
    if isinstance(board_id, dict):
        return board_id

    lists = await _call(get_client().board_lists(board_id))
    if isinstance(lists, dict):
        return lists
    cards = await _call(get_client().board_cards(board_id))
    if isinstance(cards, dict):
        return cards
    actions = await _call(get_client().board_actions(board_id, limit=200))
    if isinstance(actions, dict):
        return actions

    health = analysis.board_health(lists, cards)
    return {
        "board_id": board_id,
        "activity": analysis.activity_digest(actions, lists, window_days),
        "current_state": {
            "totals": health["totals"],
            "overdue_cards": health["overdue_cards"],
            "stale_cards": health["stale_cards"],
        },
    }


# --- write tools -----------------------------------------------------------


@server.tool()
async def create_card(
    board: str,
    list_name: str,
    name: str,
    description: str | None = None,
    due: str | None = None,
) -> dict[str, Any]:
    """Create a card on a board, in the named list. This writes to the real board.

    `due` takes an ISO-8601 timestamp such as 2026-09-01T17:00:00Z.
    """
    board_id = await _resolve_board(board)
    if isinstance(board_id, dict):
        return board_id

    lists = await _call(get_client().board_lists(board_id))
    if isinstance(lists, dict):
        return lists
    matches = [l for l in lists if list_name.lower() in (l.get("name") or "").lower()]
    if not matches:
        available = ", ".join((l.get("name") or "?") for l in lists)
        return {"error": f"No list matched {list_name!r}. Lists: {available}"}
    if len(matches) > 1:
        available = ", ".join((l.get("name") or "?") for l in matches)
        return {"error": f"{list_name!r} matched several lists: {available}. Be specific."}

    card = await _call(
        get_client().create_card(matches[0]["id"], name, description, due)
    )
    if "error" in (card or {}):
        return card
    return {
        "created": True,
        "card_id": card.get("id"),
        "name": card.get("name"),
        "list_name": matches[0].get("name"),
        "url": card.get("shortUrl") or card.get("url"),
    }


@server.tool()
async def move_card(card_id: str, to_list: str, position: str = "top") -> dict[str, Any]:
    """Move a card to a different list on the same board. This writes to the real board.

    `position` is "top", "bottom", or a numeric string.
    """
    card = await _call(get_client().card(card_id))
    if "error" in (card or {}):
        return card

    lists = await _call(get_client().board_lists(card.get("idBoard")))
    if isinstance(lists, dict):
        return lists
    matches = [l for l in lists if to_list.lower() in (l.get("name") or "").lower()]
    if len(matches) != 1:
        available = ", ".join((l.get("name") or "?") for l in lists)
        return {"error": f"{to_list!r} did not match exactly one list. Lists: {available}"}

    updated = await _call(
        get_client().update_card(card_id, idList=matches[0]["id"], pos=position)
    )
    if "error" in (updated or {}):
        return updated
    return {
        "moved": True,
        "card_id": card_id,
        "name": updated.get("name"),
        "to_list": matches[0].get("name"),
        "url": updated.get("shortUrl") or updated.get("url"),
    }


@server.tool()
async def update_card(
    card_id: str,
    name: str | None = None,
    description: str | None = None,
    due: str | None = None,
    due_complete: bool | None = None,
    archive: bool | None = None,
) -> dict[str, Any]:
    """Update a card's name, description, due date, or archive it. This writes to the real board.

    Only the arguments you pass are changed. `archive=true` closes the card,
    which hides it from the board but does not delete it.
    """
    fields: dict[str, Any] = {}
    if name is not None:
        fields["name"] = name
    if description is not None:
        fields["desc"] = description
    if due is not None:
        fields["due"] = due
    if due_complete is not None:
        fields["dueComplete"] = str(due_complete).lower()
    if archive is not None:
        fields["closed"] = str(archive).lower()
    if not fields:
        return {"error": "Nothing to update. Pass at least one field to change."}

    updated = await _call(get_client().update_card(card_id, **fields))
    if "error" in (updated or {}):
        return updated
    return {
        "updated": True,
        "card_id": card_id,
        "changed_fields": sorted(fields),
        "name": updated.get("name"),
        "url": updated.get("shortUrl") or updated.get("url"),
    }


@server.tool()
async def add_comment(card_id: str, text: str) -> dict[str, Any]:
    """Post a comment on a card as the authenticated user. This writes to the real board."""
    result = await _call(get_client().add_comment(card_id, text))
    if "error" in (result or {}):
        return result
    return {"commented": True, "card_id": card_id, "comment_id": result.get("id")}


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    server.run(transport=transport)


if __name__ == "__main__":
    main()
