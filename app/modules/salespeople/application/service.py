"""Who is which SIESA salesperson, and what each person sees of the customers.

Rules:
- The scope is decided by the server from the person's role and links, never by a
  parameter of the client.
- ``all``: platform administrators and moderators, anyone in BI or the call center, the
  leader or responsible of Comercial and Comercial's directors (``manage`` on
  ``comercial-director``, 2026-11-23).
- ``team``: a coordinator of the commercial master (``consumo.supervisores``, never SIESA's
  ``cod_supervisor``, 2026-11-24) sees the customers of the active salespeople of their team
  (``consumo.equipos``), plus their own seller codes.
- ``mine``: a person linked to seller codes sees those customers.
- ``none``: everyone else, until an administrator links them.
- A link comes from the corporate e-mail (``auto``) or from an administrator (``manual``).
  While a person has no seller link stored, their e-mail still links them live, except to
  codes someone holds or an administrator unlinked. The same goes for supervisor codes, whose
  e-mail is the coordinators' master (``consumo.supervisores``, 2026-11-24): a coordinator
  sees their team without an administrator linking them.
- Unlinking keeps the code as ``excluded``: the e-mail never links it again by itself.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.salespeople.domain.entities import (
    AUTO,
    EVERYTHING,
    EXCLUDED,
    KINDS,
    MANUAL,
    MINE,
    NOTHING,
    SELLER,
    SUPERVISOR,
    TEAM,
    RosterEntry,
    SalespersonLink,
    SalesScope,
    clean_code,
)
from app.modules.salespeople.domain.ports import LinkRepository, SalesRoster, UserLookup

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RosterRow:
    """A salesperson or supervisor with who holds the code."""

    entry: RosterEntry
    link: SalespersonLink | None
    holder_email: str | None
    holder_name: str | None


@dataclass(frozen=True)
class RosterPage:
    items: tuple[RosterRow, ...]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class SyncResult:
    """What a sync by e-mail did."""

    created: int
    matched: int
    without_user: int


class SalesScopeService:
    """Resolve each person's sales scope and manage the links."""

    def __init__(
        self,
        links: LinkRepository,
        roster: SalesRoster,
        users: UserLookup,
        access,
        audit=None,
        all_area_slugs: Sequence[str] = ("bi",),
        leader_area_slugs: Sequence[str] = ("comercial",),
        directors=None,
    ) -> None:
        """Create the service.

        Args:
            links (LinkRepository): Stored links.
            roster (SalesRoster): The sales force in the warehouse.
            users (UserLookup): Platform users.
            access: Gives ``profile(principal)`` with the person's areas (``AreaService``).
            audit: Optional ``record_global_event`` sink for the permissions log.
            all_area_slugs (Sequence[str]): Areas whose members see every customer.
            leader_area_slugs (Sequence[str]): Areas whose leader or responsible sees every
                customer.
            directors: Optional policy (``is_manager``) of Comercial's directors (permission
                ``comercial-director``): they see every customer.
        """

        self.directors = directors
        self.links = links
        self.roster = roster
        self.users = users
        self.access = access
        self.audit = audit
        self.all_area_slugs = tuple(all_area_slugs)
        self.leader_area_slugs = tuple(leader_area_slugs)

    # --- Helpers ---

    @staticmethod
    def _require_admin(principal: Principal) -> None:
        if not principal.is_platform_admin:
            raise ForbiddenError("Solo un administrador de la plataforma puede hacer esto")

    @staticmethod
    def _require_admin_or_moderator(principal: Principal) -> None:
        if not (principal.is_platform_admin or principal.is_moderator):
            raise ForbiddenError("Solo un administrador o un moderador puede ver esto")

    @staticmethod
    def _kind(kind: str) -> str:
        kind = kind.strip().lower()
        if kind not in KINDS:
            raise InvalidInputError("El tipo debe ser seller o supervisor")
        return kind

    async def _event(self, action: str, principal: Principal | None, user_id, detail) -> None:
        if self.audit is not None:
            await self.audit.record_global_event(
                action, principal, target_user_id=user_id, detail=detail
            )

    async def _sees_everything(self, principal: Principal) -> bool:
        if principal.is_platform_admin or principal.is_moderator:
            return True
        profile = await self.access.profile(principal)
        for slug, grants in profile.areas.items():
            if slug in self.all_area_slugs:
                return True
            if slug in self.leader_area_slugs and (grants.is_leader or grants.is_responsible):
                return True
        return self.directors is not None and await self.directors.is_manager(principal)

    async def _email_codes(self, email: str | None) -> list[str]:
        """Seller codes the e-mail gives, minus codes someone holds or that were unlinked."""

        if not email or "@" not in email:
            return []
        try:
            entries = await self.roster.sellers_by_email(email)
        except Exception:  # noqa: BLE001 - a warehouse outage only loses the e-mail match
            logger.warning("salespeople: e-mail match skipped, warehouse unavailable")
            return []
        codes = []
        for entry in entries:
            if await self.links.get_by_code(SELLER, entry.code) is None:
                codes.append(entry.code)
        return codes

    async def _email_supervisor_codes(self, email: str | None) -> list[str]:
        """Supervisor codes the e-mail gives (coordinators' master), minus codes someone holds
        or that were unlinked."""

        if not email or "@" not in email:
            return []
        try:
            found = await self.roster.supervisors_by_email(email)
        except Exception:  # noqa: BLE001 - a warehouse outage only loses the e-mail match
            logger.warning("salespeople: supervisor e-mail match skipped, warehouse unavailable")
            return []
        codes = []
        for code in found:
            if await self.links.get_by_code(SUPERVISOR, code) is None:
                codes.append(code)
        return codes

    # --- Scope ---

    async def scope_for(self, principal: Principal) -> SalesScope:
        """What the person sees of the customers and their quotations.

        Args:
            principal (Principal): The caller.

        Returns:
            SalesScope: ``all`` (codes ``None``), ``team``, ``mine`` or ``none``.
        """

        if await self._sees_everything(principal):
            stored = await self.links.list_for_user(principal.user_id)
            sellers = tuple(link.code for link in stored if link.kind == SELLER)
            supervisors = tuple(link.code for link in stored if link.kind == SUPERVISOR)
            return SalesScope(EVERYTHING.scope, sellers, supervisors, None)
        stored = await self.links.list_for_user(principal.user_id)
        sellers = [link.code for link in stored if link.kind == SELLER]
        supervisors = [link.code for link in stored if link.kind == SUPERVISOR]
        if not sellers:
            sellers = await self._email_codes(principal.email)
        if not supervisors:
            supervisors = await self._email_supervisor_codes(principal.email)
        if supervisors:
            team = await self.roster.team(supervisors)
            codes = tuple(sorted({*team, *sellers}))
            return SalesScope(TEAM, tuple(sellers), tuple(supervisors), codes)
        if sellers:
            return SalesScope(MINE, tuple(sellers), (), tuple(sorted(sellers)))
        return NOTHING

    # --- Administration ---

    async def roster_page(
        self,
        principal: Principal,
        *,
        kind: str = SELLER,
        q: str | None = None,
        linked: bool | None = None,
        active_only: bool = True,
        page: int = 1,
        size: int = 50,
    ) -> RosterPage:
        """Salespeople or supervisors with who holds each code.

        Args:
            principal (Principal): An administrator or a moderator.
            kind (str): ``seller`` or ``supervisor``.
            q (str | None): Part of the code, the name or the e-mail.
            linked (bool | None): Only linked (``True``) or unlinked (``False``) codes.
            active_only (bool): Leave out inactive salespeople.
            page (int): 1-based page.
            size (int): 1 to 200 rows.

        Returns:
            RosterPage: The page.
        """

        self._require_admin_or_moderator(principal)
        kind = self._kind(kind)
        if page < 1 or not 1 <= size <= 200:
            raise InvalidInputError("Página o tamaño inválido")
        entries = await (self.roster.sellers() if kind == SELLER else self.roster.supervisors())
        stored = {link.code: link for link in await self.links.list_all() if link.kind == kind}
        emails = await self.users.active_emails()
        holders = await self.users.get_users(
            {link.user_id for link in stored.values() if link.user_id}
            | {emails[e.email] for e in entries if e.email in emails}
        )
        term = (q or "").strip().lower()
        rows: list[RosterRow] = []
        for entry in entries:
            if active_only and not entry.is_active:
                continue
            link = stored.get(entry.code)
            holder_id = link.user_id if link else emails.get(entry.email or "")
            holder = holders.get(holder_id) if holder_id else None
            is_linked = holder is not None and (link is None or link.source != EXCLUDED)
            if linked is not None and is_linked != linked:
                continue
            if term and not any(
                term in (value or "").lower()
                for value in (entry.code, entry.name, entry.email, holder and holder.email)
            ):
                continue
            rows.append(
                RosterRow(
                    entry,
                    link,
                    holder.email if is_linked and holder else None,
                    holder.display_name if is_linked and holder else None,
                )
            )
        start = (page - 1) * size
        return RosterPage(tuple(rows[start : start + size]), len(rows), page, size)

    async def user_links(self, principal: Principal, email: str):
        """A person's links and resulting scope, for the panel.

        Args:
            principal (Principal): An administrator or a moderator.
            email (str): The person's corporate e-mail.

        Returns:
            tuple: ``(user, links)``.
        """

        self._require_admin_or_moderator(principal)
        user = await self.users.require_user(email)
        return user, await self.links.list_for_user(user.id)

    async def link(self, principal: Principal, email: str, kind: str, code: str) -> SalespersonLink:
        """Give a code to a person (it wins over any link by e-mail).

        Args:
            principal (Principal): An administrator.
            email (str): The person's corporate e-mail.
            kind (str): ``seller`` or ``supervisor``.
            code (str): The SIESA code.

        Returns:
            SalespersonLink: The manual link.

        Raises:
            NotFoundError: The code is not in the warehouse or the person does not exist.
            ConflictError: Someone else holds the code by hand, or the person is inactive.
        """

        self._require_admin(principal)
        kind = self._kind(kind)
        code = clean_code(code)
        if not code:
            raise InvalidInputError("Escribe el código")
        user = await self.users.require_user(email)
        if not user.is_active:
            raise ConflictError("No se puede vincular un usuario inactivo")
        entries = await (self.roster.sellers() if kind == SELLER else self.roster.supervisors())
        if not any(entry.code == code for entry in entries):
            where = (
                "un vendedor con el código {code} en SIESA"
                if kind == SELLER
                else "un coordinador con el código {code} en el maestro comercial"
            )
            raise NotFoundError(f"No hay {where.format(code=code)}")
        current = await self.links.get_by_code(kind, code)
        if current and current.user_id == user.id and current.source == MANUAL:
            return current
        if current and current.source == MANUAL and current.user_id != user.id:
            raise ConflictError(
                f"El código {code} ya está vinculado a otra persona: desvincúlalo primero"
            )
        link = await self.links.put(kind, code, user.id, MANUAL, principal.user_id)
        await self._event(
            "salesperson_linked",
            principal,
            user.id,
            {"kind": kind, "code": code, "before": current.source if current else None},
        )
        return link

    async def unlink(self, principal: Principal, link_id: UUID) -> None:
        """Take a code from its holder; the e-mail will not link it again.

        Args:
            principal (Principal): An administrator.
            link_id (UUID): The link.
        """

        self._require_admin(principal)
        link = await self.links.get(link_id)
        if link is None or link.source == EXCLUDED:
            raise NotFoundError("Vínculo no encontrado")
        await self.links.put(link.kind, link.code, None, EXCLUDED, principal.user_id)
        await self._event(
            "salesperson_unlinked",
            principal,
            link.user_id,
            {"kind": link.kind, "code": link.code, "source": link.source},
        )

    async def sync_by_email(self, principal: Principal | None) -> SyncResult:
        """Store a link for every active salesperson, and every active supervisor of the
        coordinators' master, whose e-mail is an active user's.

        Codes already linked or unlinked are left alone.

        Args:
            principal (Principal | None): An administrator, or ``None`` for a system job.

        Returns:
            SyncResult: Created links, matches and salespeople with an unknown e-mail.
        """

        if principal is not None:
            self._require_admin(principal)
        emails = await self.users.active_emails()
        matched: list[tuple[str, str, UUID]] = []
        without_user = 0
        entries = [*await self.roster.sellers(), *await self.roster.supervisors()]
        for entry in entries:
            if not entry.is_active or not entry.email:
                continue
            user_id = emails.get(entry.email)
            if user_id is None:
                without_user += 1
                continue
            matched.append((entry.kind, entry.code, user_id))
        created = await self.links.create_missing(matched, AUTO)
        if created:
            await self._event("salespeople_synced", principal, None, {"created": created})
        return SyncResult(created, len(matched), without_user)
