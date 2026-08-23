"""Deterministic metrics computed over Trello data.

These functions do no natural-language reasoning. They reduce a board to
counts, ages and outliers, and the calling model turns that into prose.
Keeping the judgement on the client side is what makes the same tools useful
to Claude Code, Claude Desktop, or any other MCP client.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

# A card untouched for this long is worth surfacing. Two working weeks is long
# enough to survive a normal sprint but short enough to catch real drift.
STALE_AFTER_DAYS = 14

# Lists whose names suggest work is in flight rather than queued or finished.
# Used only to label a list, never to hide one from the output.
IN_FLIGHT_HINTS = ("doing", "in progress", "progress", "review", "testing", "qa", "wip")
DONE_HINTS = ("done", "complete", "shipped", "released", "closed", "archive")
BLOCKED_HINTS = ("blocked", "hold", "waiting", "stuck", "paused")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    """Parse a Trello ISO-8601 timestamp, tolerating the trailing Z."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(value: str | None, now: datetime | None = None) -> float | None:
    moment = parse_time(value)
    if moment is None:
        return None
    return round(((now or _now()) - moment).total_seconds() / 86400, 1)


def classify_list(name: str) -> str:
    """Bucket a list by its name so flow can be described without a config file."""
    lowered = name.lower()
    if any(hint in lowered for hint in BLOCKED_HINTS):
        return "blocked"
    if any(hint in lowered for hint in DONE_HINTS):
        return "done"
    if any(hint in lowered for hint in IN_FLIGHT_HINTS):
        return "in_flight"
    return "backlog"


def card_digest(card: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Shrink a Trello card to the fields that matter for triage."""
    now = now or _now()
    due = parse_time(card.get("due"))
    badges = card.get("badges") or {}
    return {
        "id": card.get("id"),
        "name": card.get("name"),
        "url": card.get("shortUrl") or card.get("url"),
        "list_id": card.get("idList"),
        "labels": [lbl.get("name") or lbl.get("color") for lbl in card.get("labels") or []],
        "assignee_count": len(card.get("idMembers") or []),
        "due": card.get("due"),
        "due_complete": bool(card.get("dueComplete")),
        "overdue": bool(due and not card.get("dueComplete") and due < now),
        "idle_days": days_since(card.get("dateLastActivity"), now),
        "comment_count": badges.get("comments", 0),
        "checklist_done": badges.get("checkItemsChecked", 0),
        "checklist_total": badges.get("checkItems", 0),
    }


def board_health(
    lists: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    stale_after_days: int = STALE_AFTER_DAYS,
) -> dict[str, Any]:
    """Per-list counts plus the board-wide problems worth a human's attention."""
    now = _now()
    list_names = {lst["id"]: lst.get("name", "(unnamed)") for lst in lists}
    digests = [card_digest(card, now) for card in cards]

    by_list: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for digest in digests:
        by_list[digest["list_id"]].append(digest)

    columns = []
    for lst in lists:
        members = by_list.get(lst["id"], [])
        idle_values = [d["idle_days"] for d in members if d["idle_days"] is not None]
        columns.append(
            {
                "list_id": lst["id"],
                "list_name": lst.get("name"),
                "stage": classify_list(lst.get("name", "")),
                "card_count": len(members),
                "overdue_count": sum(1 for d in members if d["overdue"]),
                "unassigned_count": sum(1 for d in members if d["assignee_count"] == 0),
                "stale_count": sum(
                    1 for d in members if (d["idle_days"] or 0) >= stale_after_days
                ),
                "median_idle_days": _median(idle_values),
                "oldest_idle_days": max(idle_values) if idle_values else None,
            }
        )

    stale = sorted(
        (d for d in digests if (d["idle_days"] or 0) >= stale_after_days),
        key=lambda d: d["idle_days"] or 0,
        reverse=True,
    )
    overdue = sorted((d for d in digests if d["overdue"]), key=lambda d: d["due"] or "")
    label_counts = Counter(label for d in digests for label in d["labels"] if label)

    return {
        "totals": {
            "open_cards": len(digests),
            "lists": len(lists),
            "overdue": len(overdue),
            "unassigned": sum(1 for d in digests if d["assignee_count"] == 0),
            "stale": len(stale),
            "stale_threshold_days": stale_after_days,
        },
        "columns": columns,
        "overdue_cards": [_with_list(d, list_names) for d in overdue[:15]],
        "stale_cards": [_with_list(d, list_names) for d in stale[:15]],
        "top_labels": label_counts.most_common(10),
    }


def bottlenecks(
    lists: list[dict[str, Any]],
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rank lists by how much work is piling up and ageing inside them.

    The score is intentionally simple and explained in the payload, so the
    client model can disagree with it out loud rather than treat it as truth.
    """
    health = board_health(lists, cards)
    columns = [c for c in health["columns"] if c["stage"] != "done"]
    if not columns:
        return {
            "ranked_lists": [],
            "note": "No non-done lists found, so there is nothing to rank.",
            "totals": health["totals"],
        }

    max_count = max((c["card_count"] for c in columns), default=0) or 1
    max_idle = max((c["oldest_idle_days"] or 0 for c in columns), default=0) or 1

    ranked = []
    for column in columns:
        volume = column["card_count"] / max_count
        ageing = (column["oldest_idle_days"] or 0) / max_idle
        ranked.append({**column, "pressure_score": round(0.5 * volume + 0.5 * ageing, 3)})

    ranked.sort(key=lambda c: c["pressure_score"], reverse=True)
    return {
        "ranked_lists": ranked,
        "scoring": (
            "pressure_score = 0.5 * (cards in list / most cards in any list) "
            "+ 0.5 * (oldest idle days in list / oldest idle days on board). "
            "It flags where work accumulates; it does not prove a cause."
        ),
        "totals": health["totals"],
    }


def activity_digest(
    actions: list[dict[str, Any]],
    lists: list[dict[str, Any]],
    window_days: int = 7,
) -> dict[str, Any]:
    """Summarise recent board actions into movements, creations and comments."""
    now = _now()
    list_names = {lst["id"]: lst.get("name", "(unnamed)") for lst in lists}

    moves: list[dict[str, Any]] = []
    created: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    actors: Counter[str] = Counter()

    for action in actions:
        age = days_since(action.get("date"), now)
        if age is None or age > window_days:
            continue
        actor = ((action.get("memberCreator") or {}).get("fullName")) or "unknown"
        actors[actor] += 1
        data = action.get("data") or {}
        card = data.get("card") or {}
        entry = {
            "card_name": card.get("name"),
            "card_id": card.get("id"),
            "actor": actor,
            "days_ago": age,
        }

        kind = action.get("type")
        if kind == "createCard":
            created.append(entry)
        elif kind == "commentCard":
            comments.append({**entry, "text": (data.get("text") or "")[:280]})
        elif kind == "updateCard":
            before, after = data.get("listBefore"), data.get("listAfter")
            if before and after:
                moves.append(
                    {
                        **entry,
                        "from_list": before.get("name") or list_names.get(before.get("id")),
                        "to_list": after.get("name") or list_names.get(after.get("id")),
                    }
                )

    return {
        "window_days": window_days,
        "counts": {
            "cards_moved": len(moves),
            "cards_created": len(created),
            "comments": len(comments),
        },
        "moves": moves[:25],
        "created": created[:25],
        "comments": comments[:25],
        "most_active": actors.most_common(10),
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 1)
    return round((ordered[mid - 1] + ordered[mid]) / 2, 1)


def _with_list(digest: dict[str, Any], list_names: dict[str, str]) -> dict[str, Any]:
    return {**digest, "list_name": list_names.get(digest["list_id"], "(unknown list)")}
