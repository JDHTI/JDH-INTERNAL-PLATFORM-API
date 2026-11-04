"""Microsoft Entra OIDC, JWT validation, and Graph adapters."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import InvalidTokenError

from app.modules.identity.domain.entities import EntraProfile, IdentityToken


class EntraError(Exception):
    """Base error for an invalid or unavailable Entra operation."""


class EntraConfigurationError(EntraError):
    """Raised when the API is not configured for real Entra authentication."""


class EntraTokenError(EntraError):
    """Raised when a bearer token is not valid for this API."""


class EntraTokenExpired(EntraTokenError):
    """Raised when a validly signed access token has expired."""


@dataclass(frozen=True, slots=True)
class TokenSet:
    """Tokens returned by the authorization-code or refresh-token flow.

    ``access_token`` is the session token kept in the cookie: the API access token, or
    the ID token when the app registration exposes no API scope. ``graph_token`` is the
    Microsoft Graph token of the ID-token mode (``User.Read``), used once to read the profile.
    """

    access_token: str
    refresh_token: str | None
    expires_in: int
    graph_token: str | None = None


GRAPH_SCOPES = "openid profile email offline_access User.Read"


def session_tokens(payload: dict[str, Any], id_token_session: bool) -> TokenSet:
    """Pick the session token out of an Entra token response.

    Args:
        payload (dict[str, Any]): JSON of the token endpoint.
        id_token_session (bool): Whether the ID token is the session token.

    Returns:
        TokenSet: The tokens.

    Raises:
        EntraTokenError: When the token the session needs is missing.
    """

    if id_token_session:
        session = payload.get("id_token")
        if not session:
            raise EntraTokenError("Entra did not return an ID token")
        return TokenSet(
            access_token=session,
            refresh_token=payload.get("refresh_token"),
            expires_in=max(1, int(payload.get("expires_in", 3600))),
            graph_token=payload.get("access_token"),
        )
    access_token = payload.get("access_token")
    if not access_token:
        raise EntraTokenError("Entra did not return an access token")
    return TokenSet(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_in=max(1, int(payload.get("expires_in", 3600))),
    )


class EntraClient:
    """Small async client for the Entra token and Microsoft Graph endpoints."""

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        audience: str,
        api_scope: str,
        redirect_uri: str,
        authority: str,
        issuer: str,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.audience = audience
        self.api_scope = api_scope
        self.redirect_uri = redirect_uri
        self.authority = authority.rstrip("/")
        self.issuer = issuer.rstrip("/")
        self._jwks: dict[str, Any] | None = None
        self._jwks_expires_at = 0.0

    @property
    def configured(self) -> bool:
        """Return whether all values required for a real OIDC flow exist."""

        return bool(self.tenant_id and self.client_id and self.audience)

    def ensure_configured(self) -> None:
        """Raise a clear error instead of making a malformed provider request."""

        if not self.configured:
            raise EntraConfigurationError(
                "ENTRA_TENANT_ID, ENTRA_CLIENT_ID and ENTRA_API_AUDIENCE are required"
            )

    @property
    def id_token_session(self) -> bool:
        """Whether the session is the ID token (no API scope exposed by the registration)."""

        return not self.api_scope

    @property
    def api_scope_value(self) -> str:
        """Return the configured API audience and delegated scope pair."""

        return f"{self.audience.rstrip('/')}/{self.api_scope}"

    @property
    def scopes(self) -> str:
        """Scopes asked at sign-in and on every refresh."""

        if self.id_token_session:
            return GRAPH_SCOPES
        return f"{self.api_scope_value} openid profile email offline_access"

    def authorization_url(self, state: str, code_challenge: str) -> str:
        """Build the authorization-code URL used by the browser SSO redirect."""

        self.ensure_configured()
        scope = self.scopes
        query = urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "response_mode": "query",
                "scope": scope,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.authority}/oauth2/v2.0/authorize?{query}"

    async def exchange_code(self, code: str, code_verifier: str) -> TokenSet:
        """Exchange an authorization code for access and refresh cookies."""

        self.ensure_configured()
        data = {
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
            "scope": self.scopes,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        return await self._token_request(data)

    async def refresh(self, refresh_token: str) -> TokenSet:
        """Refresh an expired access token using the HttpOnly refresh cookie."""

        self.ensure_configured()
        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": self.scopes,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        return await self._token_request(data)

    async def _token_request(self, data: dict[str, str]) -> TokenSet:
        endpoint = f"{self.authority}/oauth2/v2.0/token"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(endpoint, data=data)
        except httpx.HTTPError as exc:
            raise EntraError("Entra token endpoint is unavailable") from exc
        if response.status_code >= 400:
            raise EntraTokenError("Entra rejected the token exchange")
        return session_tokens(response.json(), self.id_token_session)

    async def _get_jwks(self) -> dict[str, Any]:
        if self._jwks is not None and self._jwks_expires_at > time.monotonic():
            return self._jwks
        self.ensure_configured()
        endpoint = f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(endpoint)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EntraTokenError("Entra signing keys are unavailable") from exc
        try:
            self._jwks = response.json()
        except ValueError as exc:
            raise EntraTokenError("Entra signing keys response is invalid") from exc
        self._jwks_expires_at = time.monotonic() + 900
        return self._jwks

    async def validate_access_token(self, token: str) -> IdentityToken:
        """Validate signature and claims for an access token issued to this API."""

        if not token:
            raise EntraTokenError("Missing access token")
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise EntraTokenError("Malformed access token") from exc
        kid = header.get("kid")
        if not kid:
            raise EntraTokenError("Access token has no signing key id")
        jwks = await self._get_jwks()
        key_data = next((key for key in jwks.get("keys", []) if key.get("kid") == kid), None)
        if key_data is None:
            # A key rotation can make a cached response stale; force one refresh.
            self._jwks = None
            self._jwks_expires_at = 0
            jwks = await self._get_jwks()
            key_data = next((key for key in jwks.get("keys", []) if key.get("kid") == kid), None)
        if key_data is None:
            raise EntraTokenError("Access token signing key is unknown")
        try:
            claims = jwt.decode(
                token,
                jwt.algorithms.RSAAlgorithm.from_jwk(key_data),
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise EntraTokenExpired("Access token expired") from exc
        except InvalidTokenError as exc:
            raise EntraTokenError("Access token claims are invalid") from exc
        if claims.get("tid") != self.tenant_id:
            raise EntraTokenError("Access token belongs to another tenant")
        object_id = claims.get("oid") or claims.get("sub")
        if not object_id:
            raise EntraTokenError("Access token has no user object id")
        scopes = set(str(claims.get("scp", "")).split())
        if self.api_scope and self.api_scope not in scopes:
            raise EntraTokenError("Access token does not grant the API scope")
        return IdentityToken(
            tenant_id=self.tenant_id,
            object_id=object_id,
            name=claims.get("name") or claims.get("preferred_username"),
            email=claims.get("preferred_username") or claims.get("email"),
            access_token=token,
        )

    async def profile(
        self, identity: IdentityToken, graph_token: str | None = None
    ) -> EntraProfile:
        """Read profile and optional photo through Graph.

        Args:
            identity (IdentityToken): The validated session token.
            graph_token (str | None): Graph token of the sign-in (ID-token mode); without
                it the API access token is exchanged on behalf of the user. In ID-token
                mode without it, the token claims are the profile.

        Returns:
            EntraProfile: Name, email, job title and photo.
        """

        name = identity.name or identity.email or identity.object_id
        email = identity.email
        job_title: str | None = None
        photo: bytes | None = None
        photo_content_type: str | None = None
        if graph_token is None and not self.id_token_session:
            graph_token = await self._on_behalf_of(identity.access_token)
        if graph_token:
            headers = {"Authorization": f"Bearer {graph_token}"}
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        "https://graph.microsoft.com/v1.0/me",
                        params={"$select": "displayName,mail,userPrincipalName,jobTitle"},
                        headers=headers,
                    )
                    if response.status_code < 400:
                        payload = response.json()
                        name = payload.get("displayName") or name
                        email = payload.get("mail") or payload.get("userPrincipalName") or email
                        job_title = payload.get("jobTitle")
                    photo_response = await client.get(
                        "https://graph.microsoft.com/v1.0/me/photo/$value", headers=headers
                    )
                    if photo_response.status_code < 400 and photo_response.content:
                        photo = photo_response.content
                        photo_content_type = photo_response.headers.get("content-type")
            except (httpx.HTTPError, ValueError, AttributeError):
                # Profile claims are still authoritative enough to register; a photo is optional.
                pass
        return EntraProfile(
            tenant_id=identity.tenant_id,
            object_id=identity.object_id,
            name=name,
            email=email,
            job_title=job_title,
            photo=photo,
            photo_content_type=photo_content_type,
        )

    async def _on_behalf_of(self, user_access_token: str) -> str | None:
        if not self.client_secret:
            return None
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "requested_token_use": "on_behalf_of",
            "assertion": user_access_token,
            "scope": "https://graph.microsoft.com/.default",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(f"{self.authority}/oauth2/v2.0/token", data=data)
            if response.status_code >= 400:
                return None
            return response.json().get("access_token")
        except (httpx.HTTPError, ValueError):
            return None
