"""Redis-backed request limiting dependencies."""

from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request, Response, status
from redis.exceptions import RedisError

from app.core.errors import DependencyUnavailableError


def _client_ip(request: Request) -> str:
    """Resolve the client address without trusting arbitrary forwarded headers."""

    settings = request.app.state.settings
    peer = request.client.host if request.client else "unknown"
    if peer in settings.trusted_proxy_ips:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip() or peer
    return peer


def rate_limit(profile: str):
    """Build a FastAPI dependency for one configured rate limit profile.

    Args:
        profile (str): ``strict``, ``medium`` or ``relaxed``.

    Returns:
        Callable: A request dependency that admits or rejects the request.
    """

    async def dependency(request: Request, response: Response) -> None:
        settings = request.app.state.settings
        limit, window_seconds = settings.rate_limit_policy(profile)
        client_ip = _client_ip(request)
        identity = hashlib.sha256(client_ip.encode("utf-8")).hexdigest()
        bucket = f"{profile}:{identity}"
        redis = request.app.state.container.redis()
        try:
            decision = await redis.allow_sliding_window(bucket, limit, window_seconds)
        except (OSError, RedisError, TimeoutError) as exc:
            raise DependencyUnavailableError(
                "El servicio de protección de tráfico no está disponible"
            ) from exc

        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - decision.count))
        if not decision.allowed:
            retry_after = max(1, decision.retry_after)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Se alcanzó el límite de solicitudes. Intenta más tarde.",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

    dependency.__name__ = f"rate_limit_{profile}"
    return dependency


__all__ = ["rate_limit"]
