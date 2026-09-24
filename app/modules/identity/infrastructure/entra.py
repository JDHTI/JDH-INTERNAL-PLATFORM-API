"""Microsoft Entra ID access-token verification."""

from __future__ import annotations

import asyncio

import jwt

from app.core.errors import UnauthenticatedError
from app.modules.identity.domain.entities import VerifiedIdentity


class EntraTokenVerifier:
    """Validate access tokens issued by the configured Entra tenant.

    The token must be requested for this API (audience = the app registration's
    client id or its ``api://`` URI). Both v1 and v2 issuers of the tenant are
    accepted because the version depends on the app registration manifest.
    Signing keys come from the tenant JWKS and are cached by PyJWT.
    """

    def __init__(self, tenant_id: str, client_id: str) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self._jwks: jwt.PyJWKClient | None = None
        if tenant_id:
            self._jwks = jwt.PyJWKClient(
                f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
                cache_keys=True,
                lifespan=3600,
            )

    async def verify(self, token: str) -> VerifiedIdentity:
        """Validate a token and extract the caller's identity.

        Args:
            token (str): Raw bearer token.

        Returns:
            VerifiedIdentity: Claims of the authenticated caller.
        """

        if self._jwks is None or not self.client_id:
            raise UnauthenticatedError("La autenticación con Entra ID no está configurada")
        try:
            # Fetching the JWKS is a blocking HTTP call on cache misses.
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=[self.client_id, f"api://{self.client_id}"],
                issuer=[
                    f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
                    f"https://sts.windows.net/{self.tenant_id}/",
                ],
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise UnauthenticatedError("Token inválido o vencido") from exc

        email = claims.get("preferred_username") or claims.get("upn") or claims.get("email")
        if not email or "@" not in email:
            raise UnauthenticatedError("El token no trae un correo del usuario")
        return VerifiedIdentity(
            email=email,
            display_name=claims.get("name"),
            entra_object_id=claims.get("oid"),
        )
