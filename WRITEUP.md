# Using an existing MCP: Playwright

Notes from installing and actually using [`@playwright/mcp`](https://github.com/microsoft/playwright-mcp),
written while building Trellis. The interesting part was not that it worked, it
was what it taught me about designing my own server.

## Install

```bash
claude mcp add playwright -- npx -y @playwright/mcp@latest
```

One snag, specific to this machine: the Claude CLI needs Git Bash on Windows and
failed with a path error because Git is installed on `K:\Git`, not the default
`C:\Program Files\Git`. Setting `CLAUDE_CODE_GIT_BASH_PATH=K:\Git\bin\bash.exe`
fixed it. Nothing to do with MCP itself, but worth recording: most of the friction
in this exercise was environment, not protocol.

A freshly added server is not available in an already-running session, so rather
than restart I connected to it from a script using the MCP Python SDK as a client
([`scripts/explore_playwright_mcp.py`](scripts/explore_playwright_mcp.py)). That
turned out to be the more useful exercise, because writing the client side is what
made the protocol concrete: spawn the process, handshake, `list_tools`,
`call_tool`, read the content blocks back.

## What it exposes

24 tools. The shape of the list is the lesson:

| Group | Tools |
| --- | --- |
| Navigation | `browser_navigate`, `browser_navigate_back`, `browser_tabs` |
| Reading | `browser_snapshot`, `browser_take_screenshot`, `browser_find`, `browser_console_messages`, `browser_network_requests`, `browser_network_request` |
| Interaction | `browser_click`, `browser_type`, `browser_fill_form`, `browser_hover`, `browser_drag`, `browser_drop`, `browser_select_option`, `browser_press_key`, `browser_file_upload`, `browser_handle_dialog` |
| Escape hatches | `browser_evaluate`, `browser_run_code_unsafe` |
| Lifecycle | `browser_resize`, `browser_wait_for`, `browser_close` |

## What I used it for

I pointed it at the Trello REST API docs for the cards endpoints, which is the
surface Trellis wraps:

```
navigate  -> https://developer.atlassian.com/cloud/trello/rest/api-group-cards/
snapshot  -> 195,873 characters of accessibility tree
```

Grepping that snapshot surfaced `POST /cards`, `POST /cards/{id}/actions/comments`,
and the `idList` and `dueComplete` fields. That is an independent confirmation of
the endpoints and field names I had already written into `trello.py`, arrived at
from a different direction. Useful: docs verification is a genuinely good fit for
a browser MCP, because the alternative is trusting my memory of an API.

## What was good

**The accessibility snapshot, not screenshots.** The headline design decision is
that the primary read tool returns a structured YAML accessibility tree with
stable `[ref=eNNN]` handles, and interaction tools take those refs. So the model
reads structure and acts on identity, instead of reading pixels and acting on
coordinates. No vision round trip, no brittle "click at (412, 388)". Every
element it can see, it can address.

**Tool descriptions do real work.** `browser_snapshot` describes itself as better
than a screenshot for acting on the page. That sentence is how the model knows
which of two overlapping tools to reach for. Descriptions are not documentation
here, they are routing logic.

**It echoes the generated Playwright code.** Every call returns the `await
page.goto(...)` it ran. Nice for trust: you can see exactly what happened, and
lift it into a real test.

**Sensible flags.** `--headless --isolated` gave me a throwaway profile, so
driving a browser from a script never touched my real one.

## What was awkward

**One call blew 195,873 characters.** That is the whole lesson of the exercise in
one number. A single `browser_snapshot` on a normal docs page returns more text
than most context windows want to spend, and the overwhelming majority of it was
the Atlassian nav bar, cookie banner and footer. There is a `browser_find` tool
precisely because the maintainers know this, but nothing stops a model reaching
for the expensive tool first.

**Escape hatches are load-bearing but sharp.** `browser_run_code_unsafe` says
"unsafe" in the name and executes arbitrary JavaScript in the page. It exists
because 24 tools cannot cover the real web. Reasonable, but it means the security
boundary of this MCP is really "whatever the page can do".

**Trusting page content.** Anything the browser reads is attacker-controlled text
arriving in the model's context. A page can contain instructions aimed at the
model. Using a browser MCP means treating every snapshot as data, never as
instructions.

## What it changed in Trellis

Three concrete decisions, all downstream of that 195k number:

1. **Trim fields at the client boundary.** `trello.py` declares an explicit
   `CARD_FIELDS` list rather than accepting Trello's default card object, which is
   enormous. A tool result is spent directly out of the model's context, so the
   server, not the model, should decide what is worth paying for.

2. **Return digests, not raw records.** `card_digest()` reduces a card to the
   dozen fields that matter for triage, with `overdue` and `idle_days` computed
   server-side. Cheaper than raw JSON, and it does not make the model do date
   arithmetic to answer "what is late".

3. **Write descriptions as routing hints.** Since descriptions are what the model
   selects on, every Trellis write tool says "This writes to the real board" in
   its first lines, and the server `instructions` tell the client to confirm
   before calling them. Playwright's precedent made me treat that text as part of
   the interface rather than as a comment.

The broader takeaway: an MCP server is a context-budget design problem at least as
much as an API design problem. The protocol part is genuinely small, and the SDK
handles it. The hard question is what deserves to be in the model's context, and
that is a judgement no framework makes for you.

## Reproducing this

```bash
.venv/Scripts/python scripts/explore_playwright_mcp.py
```

Connects to `@playwright/mcp`, prints the full tool list, loads the Trello API
docs headless, and greps the snapshot for the fields Trellis depends on.
