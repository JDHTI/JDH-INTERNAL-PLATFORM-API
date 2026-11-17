"""PostgreSQL storage of the OM templates, the OM consecutive and the OMs of the cases."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.pqrsf.domain.improvement import (
    DocumentTemplate,
    ImprovementOpportunity,
    Signer,
)
from app.modules.pqrsf.infrastructure.models import (
    DocumentTemplateModel,
    ImprovementOpportunityModel,
    OmSequenceModel,
)


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
