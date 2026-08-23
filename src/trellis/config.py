"""Credential loading for the Trello REST API."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class MissingCredentials(RuntimeError):
    """Raised when the server starts without usable Trello credentials."""


@dataclass(frozen=True)
class Credentials:
    api_key: str
    token: str


def load_credentials() -> Credentials:
    """Read TRELLO_API_KEY / TRELLO_TOKEN from the environment.

    Both are required by every Trello REST call, so we fail with an
    actionable message rather than letting the API return a bare 401.
    """
    api_key = os.environ.get("TRELLO_API_KEY", "").strip()
    token = os.environ.get("TRELLO_TOKEN", "").strip()

    missing = [
        name
        for name, value in (("TRELLO_API_KEY", api_key), ("TRELLO_TOKEN", token))
        if not value
    ]
    if missing:
        raise MissingCredentials(
            f"Missing {' and '.join(missing)}. Copy .env.example to .env and fill it in, "
            "or set the variables in your MCP client config. "
            "Get both at https://trello.com/power-ups/admin"
        )
    return Credentials(api_key=api_key, token=token)
