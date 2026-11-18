"""PostgreSQL storage of the OM templates, the OM consecutive and the OMs of the cases."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import String, cast, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.pqrsf.domain.improvement import (
    DocumentTemplate,
    ImprovementOpportunity,
    OmFilters,
    OmHistoryRow,
    Signer,
)
from app.modules.pqrsf.infrastructure.models import (
    CaseEventModel,
    CaseModel,
    CaseStatusModel,
    DocumentTemplateModel,
    ImprovementOpportunityModel,
    OmSequenceModel,
)
from app.modules.sites.infrastructure.models import SiteModel


def _template(model: DocumentTemplateModel) -> DocumentTemplate:
    return DocumentTemplate(
        id=model.id,
        kind=model.kind,
        version_label=model.version_label,
        filename=model.filename,
        storage_key=model.storage_key,
        size_bytes=model.size_bytes,
        checksum_sha256=model.checksum_sha256,
        is_active=model.is_active,
        uploaded_by=model.uploaded_by,
        created_at=model.created_at,
    )


def _om(model: ImprovementOpportunityModel) -> ImprovementOpportunity:
    signer = (
        Signer(
            model.signer_user_id,
            model.signer_name,
            model.signer_position,
            model.signer_email,
            model.signer_phone,
        )
        if model.signer_user_id or model.signer_name
        else None
    )
    return ImprovementOpportunity(
        id=model.id,
        case_id=model.case_id,
        number=model.number,
        code=model.code,
        supplier_code=model.supplier_code,
        supplier_name=model.supplier_name,
        immediate_actions=model.immediate_actions,
        request_note=model.request_note,
        document_number=model.document_number,
        document_date=model.document_date,
        evidence_ids=list(model.evidence_ids or []),
        signer=signer,
        template_id=model.template_id,
        docx_key=model.docx_key,
        pdf_key=model.pdf_key,
        edited=model.edited,
        status=model.status,
        generated_at=model.generated_at,
        sent_at=model.sent_at,
        created_by=model.created_by,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _om_conditions(filters: OmFilters) -> list:
    """Filters of the history that do not depend on the reply."""

    om = ImprovementOpportunityModel
    conditions: list = []
    if filters.site_id:
        conditions.append(CaseModel.site_id == filters.site_id)
    if filters.supplier:
        pattern = f"%{filters.supplier.strip()}%"
        conditions.append(or_(om.supplier_name.ilike(pattern), om.supplier_code.ilike(pattern)))
    if filters.query:
        pattern = f"%{filters.query.strip()}%"
        conditions.append(
            or_(
                om.code.ilike(pattern),
                CaseModel.code.ilike(pattern),
                CaseModel.customer_name.ilike(pattern),
                om.supplier_name.ilike(pattern),
            )
        )
    if filters.sent_from:
        conditions.append(func.date(om.sent_at) >= filters.sent_from)
    if filters.sent_to:
        conditions.append(func.date(om.sent_at) <= filters.sent_to)
    return conditions


class SqlAlchemyImprovementRepository:
    """Templates, the OM consecutive and OMs, one short transaction per call."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    # --- Templates ---

    async def list_templates(self, kind: str) -> list[DocumentTemplate]:
        """Every version of a template, newest first."""

        query = (
            select(DocumentTemplateModel)
            .where(DocumentTemplateModel.kind == kind)
            .order_by(DocumentTemplateModel.created_at.desc())
        )
        async with self.database.session_factory() as session:
            return [_template(m) for m in (await session.scalars(query)).all()]

    async def get_template(self, template_id: UUID) -> DocumentTemplate | None:
        """One template version."""

        async with self.database.session_factory() as session:
            model = await session.get(DocumentTemplateModel, template_id)
            return _template(model) if model else None

    async def active_template(self, kind: str) -> DocumentTemplate | None:
        """The version in use for a kind."""

        query = select(DocumentTemplateModel).where(
            DocumentTemplateModel.kind == kind, DocumentTemplateModel.is_active.is_(True)
        )
        async with self.database.session_factory() as session:
            model = await session.scalar(query)
            return _template(model) if model else None

    async def add_template(self, template: DocumentTemplate) -> DocumentTemplate:
        """Store a new version and make it the only active one of its kind."""

        async with self.database.session_factory() as session:
            await session.execute(
                update(DocumentTemplateModel)
                .where(
                    DocumentTemplateModel.kind == template.kind,
                    DocumentTemplateModel.is_active.is_(True),
                )
                .values(is_active=False)
            )
            model = DocumentTemplateModel(
                id=template.id,
                kind=template.kind,
                version_label=template.version_label,
                filename=template.filename,
                storage_key=template.storage_key,
                size_bytes=template.size_bytes,
                checksum_sha256=template.checksum_sha256,
                is_active=True,
                uploaded_by=template.uploaded_by,
            )
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return _template(model)

    # --- OM consecutive ---

    async def take_om_number(self) -> int:
        """The next OM number, taken: the row lock serializes two OMs at once."""

        statement = (
            update(OmSequenceModel)
            .where(OmSequenceModel.id == 1)
            .values(next_number=OmSequenceModel.next_number + 1, updated_at=func.now())
            .returning(OmSequenceModel.next_number - 1)
        )
        async with self.database.session_factory() as session:
            number = await session.scalar(statement)
            await session.commit()
        if number is None:
            raise ConflictError("Falta el consecutivo de OM: corre las migraciones")
        return int(number)

    async def next_om_number(self) -> int:
        """The number the next OM will take."""

        async with self.database.session_factory() as session:
            model = await session.get(OmSequenceModel, 1)
            return model.next_number if model else 1

    async def set_next_om_number(self, value: int, user_id: UUID) -> None:
        """Where the OM consecutive goes on."""

        async with self.database.session_factory() as session:
            used = await session.scalar(select(func.max(ImprovementOpportunityModel.number)))
            if used is not None and value <= used:
                raise ConflictError(f"La OM {used} ya existe: el siguiente número debe ser mayor")
            await session.execute(
                update(OmSequenceModel)
                .where(OmSequenceModel.id == 1)
                .values(next_number=value, updated_by=user_id, updated_at=func.now())
            )
            await session.commit()

    # --- OMs ---

    async def list_for_case(self, case_id: UUID) -> list[ImprovementOpportunity]:
        """The OMs of a case, oldest first."""

        query = (
            select(ImprovementOpportunityModel)
            .where(ImprovementOpportunityModel.case_id == case_id)
            .order_by(ImprovementOpportunityModel.created_at)
        )
        async with self.database.session_factory() as session:
            return [_om(m) for m in (await session.scalars(query)).all()]

    async def get(self, om_id: UUID) -> ImprovementOpportunity | None:
        """One OM."""

        async with self.database.session_factory() as session:
            model = await session.get(ImprovementOpportunityModel, om_id)
            return _om(model) if model else None

    async def history(
        self, filters: OmFilters, offset: int, limit: int
    ) -> tuple[list[OmHistoryRow], int, dict[str, int]]:
        """OMs across cases, newest first, with their case and the supplier's reply.

        Returns:
            tuple: The page, the total of the filters and the counts by state (``draft``,
                ``awaiting``, ``replied``) of the filters without status and reply.
        """

        om = ImprovementOpportunityModel
        sent = aliased(CaseEventModel)
        reply = aliased(CaseEventModel)
        replied_at = (
            select(func.min(reply.created_at))
            .where(
                reply.case_id == om.case_id,
                reply.event_type == "email_received",
                om.sent_at.is_not(None),
                reply.created_at > om.sent_at,
                func.lower(reply.data["from"].astext).in_(
                    select(func.lower(func.jsonb_array_elements_text(sent.data["to"]))).where(
                        sent.case_id == om.case_id,
                        sent.event_type == "om_sent",
                        sent.data["om_id"].astext == cast(om.id, String),
                    )
                ),
            )
            .correlate(om)
            .scalar_subquery()
            .label("replied_at")
        )
        sent_to = (
            select(sent.data["to"])
            .where(
                sent.case_id == om.case_id,
                sent.event_type == "om_sent",
                sent.data["om_id"].astext == cast(om.id, String),
            )
            .order_by(sent.created_at.desc())
            .limit(1)
            .correlate(om)
            .scalar_subquery()
            .label("sent_to")
        )
        base = [*_om_conditions(filters)]
        rows_query = (
            select(
                om,
                CaseModel.code.label("case_code"),
                CaseStatusModel.name.label("case_status"),
                CaseStatusModel.is_terminal.label("case_closed"),
                CaseModel.customer_name.label("customer_name"),
                SiteModel.name.label("site_name"),
                replied_at,
                sent_to,
            )
            .join(CaseModel, CaseModel.id == om.case_id)
            .join(CaseStatusModel, CaseStatusModel.id == CaseModel.status_id)
            .outerjoin(SiteModel, SiteModel.id == CaseModel.site_id)
            .where(*base)
        )
        if filters.status:
            rows_query = rows_query.where(om.status == filters.status)
        page = rows_query.subquery()
        conditions = []
        if filters.replied is True:
            conditions.append(page.c.replied_at.is_not(None))
        elif filters.replied is False:
            conditions.extend([page.c.status == "sent", page.c.replied_at.is_(None)])
        async with self.database.session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(page).where(*conditions))
            ids = (
                (
                    await session.execute(
                        select(page.c.id)
                        .where(*conditions)
                        .order_by(page.c.sent_at.desc().nulls_first(), page.c.created_at.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            found = (await session.execute(rows_query.where(om.id.in_(ids)))).all() if ids else []
            everything = (
                select(om.status, replied_at)
                .join(CaseModel, CaseModel.id == om.case_id)
                .outerjoin(SiteModel, SiteModel.id == CaseModel.site_id)
                .where(*base)
                .subquery()
            )
            counts = (
                await session.execute(
                    select(
                        func.count().filter(everything.c.status == "draft"),
                        func.count().filter(
                            everything.c.status == "sent", everything.c.replied_at.is_(None)
                        ),
                        func.count().filter(everything.c.replied_at.is_not(None)),
                    )
                )
            ).one()
        by_id = {
            model.id: OmHistoryRow(
                om=_om(model),
                case_code=code,
                case_status=status_name,
                case_closed=bool(closed),
                customer_name=customer,
                site_name=site,
                replied_at=answered,
                sent_to=tuple(addresses or ()),
            )
            for model, code, status_name, closed, customer, site, answered, addresses in found
        }
        return (
            [by_id[i] for i in ids if i in by_id],
            int(total or 0),
            {"draft": int(counts[0]), "awaiting": int(counts[1]), "replied": int(counts[2])},
        )

    async def save(self, om: ImprovementOpportunity) -> ImprovementOpportunity:
        """Insert or update an OM."""

        signer = om.signer
        values = {
            "case_id": om.case_id,
            "number": om.number,
            "code": om.code,
            "supplier_code": om.supplier_code,
            "supplier_name": om.supplier_name,
            "immediate_actions": om.immediate_actions,
            "request_note": om.request_note,
            "document_number": om.document_number,
            "document_date": om.document_date,
            "evidence_ids": list(om.evidence_ids),
            "signer_user_id": signer.user_id if signer else None,
            "signer_name": signer.name if signer else None,
            "signer_position": signer.position if signer else None,
            "signer_email": signer.email if signer else None,
            "signer_phone": signer.phone if signer else None,
            "template_id": om.template_id,
            "docx_key": om.docx_key,
            "pdf_key": om.pdf_key,
            "edited": om.edited,
            "status": om.status,
            "generated_at": om.generated_at,
            "sent_at": om.sent_at,
            "created_by": om.created_by,
        }
        async with self.database.session_factory() as session:
            model = await session.get(ImprovementOpportunityModel, om.id)
            if model is None:
                model = ImprovementOpportunityModel(id=om.id, **values)
                session.add(model)
            else:
                for name, value in values.items():
                    setattr(model, name, value)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("El caso ya tiene una OM para ese proveedor") from exc
            await session.refresh(model)
            return _om(model)
