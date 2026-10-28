"""Move the supporting files of supports into the supports folder of the configured area.

The proofs of the supports master are filed in the folder ``SUPPLIER_SUPPORTS_DOCUMENTS_FOLDER``
of the area ``SUPPLIER_SUPPORTS_DOCUMENTS_AREA_SLUG`` (BI). Files uploaded while another area
was configured stay in that area's folder; this moves them: it copies the object to its new key,
re-points the document to the new area and folder, removes the old object and, when it is left
empty, the old folder. The links to supports follow the document, so nothing else changes.

Runs as a dry run unless ``--apply`` is given and can be repeated.

Usage:
    poetry run python scripts/relocate-support-documents.py
    poetry run python scripts/relocate-support-documents.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.app_container import (
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings
from app.modules.documents.domain.entities import storage_key_for
from app.modules.documents.infrastructure.models import DocumentFolderModel, DocumentModel
from app.modules.identity.infrastructure.models import AreaModel


async def run(apply: bool) -> int:
    settings = Settings()
    area_slug = settings.SUPPLIER_SUPPORTS_DOCUMENTS_AREA_SLUG.strip()
    folder_name = settings.SUPPLIER_SUPPORTS_DOCUMENTS_FOLDER.strip()
    container = build_application_container(settings)
    resources = await initialize_global_resources(container)
    try:
        database, storage = resources.database, resources.storage
        async with database.session_factory() as session:
            area = (
                await session.execute(select(AreaModel).where(AreaModel.slug == area_slug))
            ).scalar_one_or_none()
            if area is None:
                print(f"No existe el área «{area_slug}».")
                return 1
            folders = (
                (
                    await session.execute(
                        select(DocumentFolderModel).where(
                            func.lower(DocumentFolderModel.name) == folder_name.lower()
                        )
                    )
                )
                .scalars()
                .all()
            )
            target = next((item for item in folders if item.area_id == area.id), None)
            strays = [item for item in folders if item.area_id != area.id]
            documents = [
                document
                for folder in strays
                for document in (
                    await session.execute(
                        select(DocumentModel).where(DocumentModel.folder_id == folder.id)
                    )
                )
                .scalars()
                .all()
            ]
            print(
                f"Destino: {area.slug}/{folder_name}"
                f"{'' if target else ' (se creará)'}; por mover: {len(documents)} documento(s) "
                f"de {len(strays)} carpeta(s) de otra área."
            )
            if not documents or not apply:
                for document in documents:
                    print(f"  - {document.name}.{document.extension} ({document.size_bytes} bytes)")
                return 0
            if target is None:
                target = DocumentFolderModel(
                    id=uuid.uuid4(),
                    area_id=area.id,
                    name=folder_name,
                    description="Soportes de los apoyos de proveedores",
                    created_by="relocate-support-documents",
                )
                session.add(target)
                await session.flush()
            taken = {
                name.lower()
                for (name,) in (
                    await session.execute(
                        select(DocumentModel.name).where(DocumentModel.folder_id == target.id)
                    )
                ).all()
            }
            moved = 0
            for document in documents:
                old_key = document.storage_key
                new_key = storage_key_for(area.id, target.id, document.id, document.extension)
                body = await storage.get_object(old_key)
                await storage.put_object(new_key, body, document.content_type)
                name = document.name
                if name.lower() in taken:
                    name = f"{name} {document.id.hex[:6]}"
                taken.add(name.lower())
                document.name = name
                document.area_id = area.id
                document.folder_id = target.id
                document.storage_key = new_key
                try:
                    await session.commit()
                except Exception:
                    await session.rollback()
                    await storage.delete_object(new_key)
                    raise
                await storage.delete_object(old_key)
                moved += 1
                print(f"  movido: {name}.{document.extension}")
            for folder in strays:
                left = (
                    await session.execute(
                        select(func.count())
                        .select_from(DocumentModel)
                        .where(DocumentModel.folder_id == folder.id)
                    )
                ).scalar_one()
                if left == 0:
                    await session.delete(folder)
                    print(f"  carpeta vacía eliminada (área {folder.area_id})")
            await session.commit()
            print(f"Listo: {moved} documento(s) movido(s).")
        return 0
    finally:
        await shutdown_global_resources(resources)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    return asyncio.run(run(parser.parse_args().apply))


if __name__ == "__main__":
    raise SystemExit(main())
