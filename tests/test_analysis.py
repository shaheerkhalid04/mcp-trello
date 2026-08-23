"""Offline checks for the metric layer. No credentials and no network needed.

Run: .venv/Scripts/python -m pytest tests/ -q
  or .venv/Scripts/python tests/test_analysis.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trellis import analysis

NOW = datetime.now(timezone.utc)


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


LISTS = [
    {"id": "l1", "name": "Backlog"},
    {"id": "l2", "name": "In Progress"},
    {"id": "l3", "name": "Blocked"},
    {"id": "l4", "name": "Done"},
]

CARDS = [
    # Fresh, assigned, no due date.
    {
        "id": "c1", "name": "Write spec", "idList": "l1", "idMembers": ["m1"],
        "labels": [{"name": "docs"}], "dateLastActivity": iso(1),
        "badges": {"comments": 2, "checkItems": 0, "checkItemsChecked": 0},
    },
    # Overdue and unassigned.
    {
        "id": "c2", "name": "Fix login bug", "idList": "l2", "idMembers": [],
        "labels": [{"name": "bug"}], "due": iso(3), "dueComplete": False,
        "dateLastActivity": iso(2),
        "badges": {"comments": 0, "checkItems": 3, "checkItemsChecked": 1},
    },
    # Stale: untouched for 40 days.
    {
        "id": "c3", "name": "Migrate database", "idList": "l2", "idMembers": ["m2"],
        "labels": [{"name": "bug"}], "dateLastActivity": iso(40),
        "badges": {"comments": 1, "checkItems": 0, "checkItemsChecked": 0},
    },
    # Due in the future, so not overdue.
    {
        "id": "c4", "name": "Ship release", "idList": "l3", "idMembers": ["m1"],
        "labels": [], "due": iso(-5), "dueComplete": False, "dateLastActivity": iso(6),
        "badges": {"comments": 0, "checkItems": 0, "checkItemsChecked": 0},
    },
    # Past due but marked complete, so not overdue.
    {
        "id": "c5", "name": "Old finished task", "idList": "l4", "idMembers": ["m1"],
        "labels": [], "due": iso(20), "dueComplete": True, "dateLastActivity": iso(19),
        "badges": {"comments": 0, "checkItems": 0, "checkItemsChecked": 0},
    },
]


def test_classify_list():
    assert analysis.classify_list("Done") == "done"
    assert analysis.classify_list("In Progress") == "in_flight"
    assert analysis.classify_list("Blocked") == "blocked"
    assert analysis.classify_list("Backlog") == "backlog"
    assert analysis.classify_list("Ideas") == "backlog"


def test_card_digest_flags_overdue_correctly():
    digests = {c["id"]: analysis.card_digest(c, NOW) for c in CARDS}
    assert digests["c2"]["overdue"] is True, "past due and incomplete is overdue"
    assert digests["c4"]["overdue"] is False, "future due date is not overdue"
    assert digests["c5"]["overdue"] is False, "dueComplete cancels overdue"
    assert digests["c1"]["overdue"] is False, "no due date is not overdue"
    assert digests["c2"]["assignee_count"] == 0
    assert digests["c3"]["idle_days"] >= 39


def test_board_health_totals():
    report = analysis.board_health(LISTS, CARDS, stale_after_days=14)
    totals = report["totals"]
    assert totals["open_cards"] == 5
    assert totals["lists"] == 4
    assert totals["overdue"] == 1, "only c2 is overdue"
    assert totals["unassigned"] == 1, "only c2 has no members"
    assert totals["stale"] == 2, "c3 at 40d and c5 at 19d exceed the 14d threshold"

    in_progress = next(c for c in report["columns"] if c["list_name"] == "In Progress")
    assert in_progress["card_count"] == 2
    assert in_progress["overdue_count"] == 1
    assert in_progress["stage"] == "in_flight"

    assert report["overdue_cards"][0]["name"] == "Fix login bug"
    assert report["overdue_cards"][0]["list_name"] == "In Progress"
    assert report["stale_cards"][0]["name"] == "Migrate database", "oldest first"
    assert dict(report["top_labels"])["bug"] == 2


def test_stale_threshold_is_respected():
    strict = analysis.board_health(LISTS, CARDS, stale_after_days=5)
    loose = analysis.board_health(LISTS, CARDS, stale_after_days=100)
    assert strict["totals"]["stale"] == 3
    assert loose["totals"]["stale"] == 0


def test_bottlenecks_excludes_done_and_ranks_pressure():
    result = analysis.bottlenecks(LISTS, CARDS)
    names = [c["list_name"] for c in result["ranked_lists"]]
    assert "Done" not in names, "done lists are not bottlenecks"
    assert len(names) == 3
    assert names[0] == "In Progress", "most cards plus the oldest card should rank first"
    scores = [c["pressure_score"] for c in result["ranked_lists"]]
    assert scores == sorted(scores, reverse=True), "must be sorted descending"
    assert "pressure_score =" in result["scoring"], "formula is disclosed to the client"


def test_bottlenecks_handles_board_with_only_done_lists():
    result = analysis.bottlenecks([{"id": "l4", "name": "Done"}], [])
    assert result["ranked_lists"] == []
    assert "nothing to rank" in result["note"]


def test_activity_digest_windows_and_classifies():
    actions = [
        {
            "type": "updateCard", "date": iso(2),
            "memberCreator": {"fullName": "Ada"},
            "data": {
                "card": {"id": "c2", "name": "Fix login bug"},
                "listBefore": {"id": "l1", "name": "Backlog"},
                "listAfter": {"id": "l2", "name": "In Progress"},
            },
        },
        {
            "type": "createCard", "date": iso(1),
            "memberCreator": {"fullName": "Ada"},
            "data": {"card": {"id": "c1", "name": "Write spec"}},
        },
        {
            "type": "commentCard", "date": iso(3),
            "memberCreator": {"fullName": "Grace"},
            "data": {"card": {"id": "c3", "name": "Migrate database"}, "text": "blocked on ops"},
        },
        # Outside a 7 day window, must be dropped.
        {
            "type": "createCard", "date": iso(30),
            "memberCreator": {"fullName": "Grace"},
            "data": {"card": {"id": "c9", "name": "Ancient card"}},
        },
        # A non-move update must not be counted as a move.
        {
            "type": "updateCard", "date": iso(1),
            "memberCreator": {"fullName": "Ada"},
            "data": {"card": {"id": "c1", "name": "Write spec"}, "old": {"desc": ""}},
        },
    ]
    digest = analysis.activity_digest(actions, LISTS, window_days=7)
    assert digest["counts"]["cards_moved"] == 1
    assert digest["counts"]["cards_created"] == 1, "the 30-day-old card is outside the window"
    assert digest["counts"]["comments"] == 1
    assert digest["moves"][0]["from_list"] == "Backlog"
    assert digest["moves"][0]["to_list"] == "In Progress"
    assert dict(digest["most_active"])["Ada"] == 3


def test_handles_empty_board():
    report = analysis.board_health([], [])
    assert report["totals"]["open_cards"] == 0
    assert report["columns"] == []


def test_parse_time_tolerates_junk():
    assert analysis.parse_time(None) is None
    assert analysis.parse_time("not a date") is None
    assert analysis.parse_time("2026-01-01T00:00:00Z") is not None


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except AssertionError as exc:
                print(f"  FAIL  {name}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
