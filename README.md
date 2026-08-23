# Trellis

An MCP server that reads a Trello workspace and explains how the work is actually flowing.

Trello tells you what is on the board. Trellis tells you what that means: which column
work is piling up in, which cards have not moved in two weeks, what changed since
Monday, and what is overdue and unowned. Every capability is exposed as a real Model
Context Protocol tool, so the same server works in Claude Code, Claude Desktop, or any
other MCP-compatible client.

## Design note: no second LLM

The analysis tools do not call a language model. They compute counts, ages and outliers
from the Trello API and return structured metrics, along with the formula used to rank
anything. The client's model reads those numbers and does the reasoning.

That is deliberate. It means the server needs one credential instead of two, it stays
cheap and fast, its output is reproducible, and the model can openly disagree with a
heuristic instead of inheriting a verdict it cannot inspect.

## Tools

### Data

| Tool | What it does |
| --- | --- |
| `list_boards` | Every board the account can open, with ids and URLs. Start here. |
| `get_board_info` | One board's metadata: description, visibility, members, last activity. |
| `list_board_lists` | The open lists (columns) on a board, with card counts and an inferred stage. |
| `list_cards` | Open cards on a board, optionally filtered to one list, as triage digests. |
| `get_card` | One card in full: description, labels, due date, checklists, recent comments. |
| `search_trello` | Search cards and boards, including Trello operators like `label:bug` or `due:week`. |

### Analysis

| Tool | What it does |
| --- | --- |
| `analyze_board_health` | Per-list load plus the overdue, unassigned and stalled cards by name. |
| `detect_bottlenecks` | Ranks non-done lists by a volume-and-age pressure score, formula included. |
| `generate_standup_report` | What moved, what was created and what was discussed in the last N days. |

### Writes

These change the real board immediately.

| Tool | What it does |
| --- | --- |
| `create_card` | Add a card to a named list, with optional description and due date. |
| `move_card` | Move a card to another list on the same board. |
| `update_card` | Change name, description, due date, due-complete, or archive the card. |
| `add_comment` | Post a comment on a card as the authenticated user. |

Boards can be given by id **or by name**, so `analyze_board_health("Sprint Board")`
works without looking an id up first.

## Setup

### 1. Get Trello credentials

1. Go to [trello.com/power-ups/admin](https://trello.com/power-ups/admin) and create a
   Power-Up (any name; it exists only to give you an API key).
2. Open it and copy the **API key**.
3. Next to the key, click the **Token** link, authorize, and copy the **token**.

The token is tied to your Trello account and grants exactly the access you approve.
Treat it like a password: it is not in git, and `.env` is gitignored.

### 2. Install

```bash
python -m venv .venv && .venv/Scripts/pip install -e .
```

On macOS or Linux use `.venv/bin/pip` instead.

### 3. Configure

```bash
cp .env.example .env
```

Then fill in `TRELLO_API_KEY` and `TRELLO_TOKEN`.

## Running it

### Local smoke test

```bash
.venv/Scripts/python scripts/smoke_test.py
```

This hits the real Trello API read-only and prints what it finds, which is the fastest
way to confirm your credentials work before wiring up a client.

### In Claude Code

```bash
claude mcp add trellis -e TRELLO_API_KEY=your_key -e TRELLO_TOKEN=your_token -- /absolute/path/to/.venv/Scripts/python.exe -m trellis.server
```

### In Claude Desktop

Add this to `claude_desktop_config.json`, then restart Claude Desktop:

```json
{
  "mcpServers": {
    "trellis": {
      "command": "C:\\absolute\\path\\to\\.venv\\Scripts\\python.exe",
      "args": ["-m", "trellis.server"],
      "env": {
        "TRELLO_API_KEY": "your_key",
        "TRELLO_TOKEN": "your_token"
      }
    }
  }
}
```

### With the MCP Inspector

```bash
npx @modelcontextprotocol/inspector .venv/Scripts/python.exe -m trellis.server
```

### Over HTTP

Set `MCP_TRANSPORT=streamable-http` to serve over HTTP instead of stdio. This is the
mode a hosted deployment uses.

## Things worth knowing

- **Rate limits.** Trello allows 300 requests per 10 seconds per API key. The analysis
  tools use three or four calls each, so normal use is nowhere near the ceiling, but a
  429 is reported back in plain language if you hit one.
- **Stage detection is name-based.** A list is called done, blocked, in-flight or
  backlog by matching words in its name. A board with unusual column names will have
  its stages mislabeled, and the card counts are still correct regardless.
- **404 means invisible, not absent.** Trello returns 404 rather than 403 for a board
  you cannot see, so a 404 may mean the token lacks access rather than a bad id.
- **Archived cards are excluded** from board reads. `update_card(archive=true)` closes
  a card rather than deleting it; nothing in this server deletes anything.

## Example prompts

Once it is connected, these all work in plain language:

- "Which board has the most stalled work?"
- "Give me a standup update for the Sprint board covering the last 3 days."
- "What is overdue and unassigned across my boards?"
- "Where is work piling up on the Engineering board, and does the data actually support that?"
- "Add a card to Backlog called 'Rotate Trello token' due next Friday."

## License

MIT
