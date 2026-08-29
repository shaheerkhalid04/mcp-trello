"""Credential loading for the Trello REST API."""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class MissingCredentials(RuntimeError):
    """Raised when the server starts without usable Trello credentials."""


@dataclass(frozen=True)
class Credentials:
    api_key: str
    token: str


# Credentials for the request currently being served.
#
# Running locally over stdio there is one user and the environment is enough.
# Hosted on Smithery one process serves many users, and each request carries its
# own config, so a module-level global would let one caller's token leak into
# another caller's request. A ContextVar is scoped to the task handling the
# request, which is the boundary we actually want.
_session_credentials: ContextVar[Credentials | None] = ContextVar(
    "trellis_session_credentials", default=None
)


def set_session_credentials(api_key: str, token: str):
    """Bind credentials to the current request. Returns a reset token."""
    return _session_credentials.set(Credentials(api_key=api_key, token=token))


def reset_session_credentials(reset_token) -> None:
    _session_credentials.reset(reset_token)


def load_credentials() -> Credentials:
    """Resolve credentials for this request.

    Per-request config wins over the environment, so the same build works both
    as a local stdio server and as a hosted multi-tenant one.
    """
    scoped = _session_credentials.get()
    if scoped is not None:
        return scoped

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
