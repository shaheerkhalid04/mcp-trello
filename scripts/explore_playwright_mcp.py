"""Use an existing third-party MCP server (Playwright) as a client.

This is the "explore existing MCPs" half of the exercise, done from code rather
than from a chat window, so the transcript is reproducible. It connects to
@playwright/mcp over stdio, lists what it exposes, and drives a real browser to
read the Trello API docs that Trellis wraps.

Run: .venv/Scripts/python scripts/explore_playwright_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys

from mcp import Client, StdioServerParameters, stdio_client

TARGET = "https://developer.atlassian.com/cloud/trello/rest/api-group-cards/"


def text_of(result) -> str:
    """Flatten an MCP tool result into plain text for printing."""
    parts = []
    for block in result.content or []:
        chunk = getattr(block, "text", None)
        if chunk:
            parts.append(chunk)
    if not parts and getattr(result, "structured_content", None):
        parts.append(json.dumps(result.structured_content, indent=2)[:2000])
    return "\n".join(parts)


async def main() -> int:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if not npx:
        print("npx not found on PATH; install Node.js first.")
        return 1

    params = StdioServerParameters(
        command=npx,
        args=["-y", "@playwright/mcp@latest", "--headless", "--isolated"],
    )

    async with Client(stdio_client(params)) as client:
        info = client.server_info
        print(f"connected to: {info.name} v{info.version}")

        tools = (await client.list_tools()).tools
        print(f"\nPlaywright MCP exposes {len(tools)} tools:\n")
        for tool in tools:
            summary = (tool.description or "").strip().split("\n")[0]
            print(f"  {tool.name:34} {summary[:70]}")

        names = {t.name for t in tools}
        navigate = "browser_navigate"
        snapshot = "browser_snapshot"
        if navigate not in names:
            print(f"\nExpected {navigate} but it is missing; tool names may have changed.")
            return 1

        print(f"\n\nnavigating to {TARGET}")
        result = await client.call_tool(navigate, {"url": TARGET})
        print(text_of(result)[:600])

        if snapshot in names:
            print("\n\ntaking accessibility snapshot (this is what the model reads)")
            snap = text_of(await client.call_tool(snapshot, {}))
            print(f"snapshot length: {len(snap)} chars")
            print("\nfirst 1500 chars:\n")
            print(snap[:1500])

            hits = [
                line
                for line in snap.splitlines()
                if any(kw in line.lower() for kw in ("idlist", "post /cards", "duecomplete"))
            ]
            if hits:
                print(f"\n\nlines mentioning the card fields Trellis uses ({len(hits)}):")
                for line in hits[:12]:
                    print("  " + line.strip()[:110])

    print("\ndone. browser was headless and isolated, so no profile was touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
