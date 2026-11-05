"""The two session modes of the Entra client: API access token, or ID token."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from app.modules.identity.infrastructure.entra import (
    GRAPH_SCOPES,
    EntraClient,
    EntraTokenError,
    session_tokens,
)


def _client(api_scope: str) -> EntraClient:
    return EntraClient(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        audience="client",
        api_scope=api_scope,
        redirect_uri="https://intranet.jdh.local/api/v1/auth/callback",
        authority="https://login.microsoftonline.com/tenant",
        issuer="https://login.microsoftonline.com/tenant/v2.0",
    )


def _query(url: str) -> dict[str, str]:
    parts = urlsplit(url)
    assert parts.path == "/tenant/oauth2/v2.0/authorize"
    return {key: values[0] for key, values in parse_qs(parts.query).items()}


def test_with_an_api_scope_the_session_is_the_api_access_token() -> None:
    client = _client("access_as_user")

    query = _query(client.authorization_url("state", "challenge"))

    assert client.id_token_session is False
    assert query["scope"].split() == [
        "client/access_as_user",
        "openid",
        "profile",
        "email",
        "offline_access",
    ]
    tokens = session_tokens({"access_token": "api", "id_token": "id"}, False)
    assert (tokens.access_token, tokens.graph_token) == ("api", None)


def test_without_an_api_scope_the_session_is_the_id_token() -> None:
    client = _client("")

    query = _query(client.authorization_url("state", "challenge"))

    assert client.id_token_session is True
    assert query["scope"] == GRAPH_SCOPES
    assert query["redirect_uri"] == "https://intranet.jdh.local/api/v1/auth/callback"
    tokens = session_tokens(
        {"access_token": "graph", "id_token": "id", "refresh_token": "r", "expires_in": 3599},
        True,
    )
    assert (tokens.access_token, tokens.graph_token, tokens.refresh_token) == ("id", "graph", "r")
    with pytest.raises(EntraTokenError):
        session_tokens({"access_token": "graph"}, True)
