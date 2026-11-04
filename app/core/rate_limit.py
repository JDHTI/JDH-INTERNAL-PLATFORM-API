"""Redis-backed request counting shared by the rate-limit middleware and the public guard."""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass

from fastapi import Request
from redis.exceptions import RedisError

from app.core.errors import DependencyUnavailableError


def _trusted(address: str, names: frozenset[str], networks) -> bool:
    if address in names:
        return True
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in networks)


def client_ip(request: Request) -> str:
    """Resolve the client address without trusting arbitrary forwarded headers.

    ``X-Forwarded-For`` counts only when the peer is a trusted proxy (an address or a
    network of ``RATE_LIMIT_TRUSTED_PROXY_IPS``: Caddy, or the UI rendering a page). The
    chain is read from the right, skipping trusted hops, so a client cannot pick its own
    address by sending the header itself.
    """

    settings = request.app.state.settings
    peer = request.client.host if request.client else "unknown"
    names = settings.trusted_proxy_ips
    networks = settings.trusted_proxy_networks
    if not _trusted(peer, names, networks):
        return peer
    hops = [hop.strip() for hop in request.headers.get("X-Forwarded-For", "").split(",")]
    hops = [hop for hop in hops if hop]
    for hop in reversed(hops):
        if not _trusted(hop, names, networks):
            return hop
    return hops[0] if hops else peer


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
        profile (str): ``read``, ``relaxed``, ``strict`` or ``public_global``.
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


__all__ = ["LimitResult", "check_limit", "client_ip"]
