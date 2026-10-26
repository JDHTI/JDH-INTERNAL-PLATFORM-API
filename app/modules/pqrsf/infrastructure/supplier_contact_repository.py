"""PostgreSQL repository of the supplier contacts cases wrote to."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Database
from app.modules.pqrsf.domain.supplier_messages import SupplierContact
from app.modules.pqrsf.infrastructure.models import SupplierContactModel


def _contact(model: SupplierContactModel) -> SupplierContact:
    return SupplierContact(
        id=model.id,
        supplier_code=model.supplier_code,
        supplier_name=model.supplier_name,
        email=model.email,
        contact_name=model.contact_name,
        last_used_at=model.last_used_at,
    )


class SqlAlchemySupplierContactRepository:
    """Persist supplier contacts with short-lived transactions."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def search(
        self, supplier_code: str | None, query: str | None, limit: int
    ) -> list[SupplierContact]:
        """Find contacts of a supplier, or by address or name, most recently used first.

        Args:
            supplier_code (str | None): Only this supplier's contacts.
            query (str | None): Part of the address, contact or supplier name.
            limit (int): Most results to return.

        Returns:
            list[SupplierContact]: Matching contacts.
        """

        statement = select(SupplierContactModel)
        if supplier_code:
            statement = statement.where(SupplierContactModel.supplier_code == supplier_code)
        if query:
            statement = statement.where(
                or_(
                    SupplierContactModel.email.icontains(query, autoescape=True),
                    SupplierContactModel.contact_name.icontains(query, autoescape=True),
                    SupplierContactModel.supplier_name.icontains(query, autoescape=True),
                )
            )
        statement = statement.order_by(
            SupplierContactModel.last_used_at.desc().nulls_last(), SupplierContactModel.email
        ).limit(limit)
        async with self.database.session_factory() as session:
            return [_contact(model) for model in (await session.execute(statement)).scalars()]

    async def remember(self, contacts: list[SupplierContact]) -> None:
        """Insert new contacts and refresh when the known ones were last used.

        Args:
            contacts (list[SupplierContact]): The addresses just written to.

        Returns:
            None
        """

        if not contacts:
            return
        statement = insert(SupplierContactModel).values(
            [
                {
                    "id": contact.id,
                    "supplier_code": contact.supplier_code,
                    "supplier_name": contact.supplier_name,
                    "email": contact.email,
                    "last_used_at": contact.last_used_at,
                }
                for contact in contacts
            ]
        )
        statement = statement.on_conflict_do_update(
            constraint="pqrsf_supplier_contacts_code_email_key",
            set_={
                "last_used_at": statement.excluded.last_used_at,
                "supplier_name": func.coalesce(
                    statement.excluded.supplier_name, SupplierContactModel.supplier_name
                ),
            },
        )
        async with self.database.session_factory() as session:
            await session.execute(statement)
            await session.commit()
