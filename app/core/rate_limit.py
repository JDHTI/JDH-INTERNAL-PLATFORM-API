"""Redis-backed request limiting dependencies."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response, status
from redis.exceptions import RedisError

from app.core.errors import DependencyUnavailableError


def client_ip(request: Request) -> str:
    """Resolve the client address without trusting arbitrary forwarded headers."""

    settings = request.app.state.settings
    peer = request.client.host if request.client else "unknown"
    if peer in settings.trusted_proxy_ips:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip() or peer
    return peer


@dataclass(frozen=True)
class LimitResult:
    """Outcome of counting one request against a limit."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int


async def check_limit(request: Request, profile: str, *, shared: bool = False) -> LimitResult:
    """Count the request against a profile and say whether it fits.

    Args:
        request (Request): The incoming request.
        profile (str): ``strict``, ``medium``, ``relaxed`` or ``public_global``.
        shared (bool): One bucket for every visitor instead of one per address.

    Raises:
        DependencyUnavailableError: Redis is not reachable, so nothing can be counted.
    """

    settings = request.app.state.settings
    limit, window_seconds = settings.rate_limit_policy(profile)
    identity = "all" if shared else hashlib.sha256(client_ip(request).encode("utf-8")).hexdigest()
    redis = request.app.state.container.redis()
    try:
        decision = await redis.allow_sliding_window(f"{profile}:{identity}", limit, window_seconds)
    except (OSError, RedisError, TimeoutError) as exc:
        raise DependencyUnavailableError(
            "El servicio de protección de tráfico no está disponible"
        ) from exc
    return LimitResult(
        allowed=decision.allowed,
        limit=limit,
        remaining=max(0, limit - decision.count),
        retry_after=max(1, decision.retry_after),
    )


def rate_limit(profile: str):
    """Build a FastAPI dependency for one configured rate limit profile.

    Args:
        profile (str): ``strict``, ``medium`` or ``relaxed``.

    Returns:
        Callable: A request dependency that admits or rejects the request.
    """

    async def dependency(request: Request, response: Response) -> None:
        result = await check_limit(request, profile)
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Se alcanzó el límite de solicitudes. Intenta más tarde.",
                headers={
                    "Retry-After": str(result.retry_after),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

    dependency.__name__ = f"rate_limit_{profile}"
    return dependency


__all__ = ["LimitResult", "check_limit", "client_ip", "rate_limit"]
