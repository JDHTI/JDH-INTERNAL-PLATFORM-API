"""Load a document template into the bucket and make it the active version.

The OM template (P.06.F.04 V2) ships with the code; after a deploy it goes to the bucket
once, from the API container::

    docker exec platform-api python -m app.modules.pqrsf.templates --kind om --version V02

``--file`` loads another Word instead (a new version of the format with the same marks).
Calidad can also upload later versions from the platform (``POST /pqrsf/templates/om``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.app_container import (
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings
from app.core.errors import ApplicationError

BUNDLED = {"om": Path(__file__).parent / "assets" / "templates" / "om-p06-v02.docx"}


async def _install(kind: str, path: Path, version: str, force: bool) -> int:
    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    try:
        service = container.v1_container.pqrsf_om_service()
        active = await service.repository.active_template(kind)
        if active is not None and not force:
            print(
                f"Ya hay una plantilla activa ({active.version_label}, {active.filename}). "
                "Usa --force para reemplazarla."
            )
            return 0
        template = await service.install_template(kind, path.name, path.read_bytes(), version, None)
    except ApplicationError as exc:
        print(f"No se cargó la plantilla: {exc}", file=sys.stderr)
        return 1
    finally:
        await shutdown_global_resources(resources)
    print(f"Plantilla {kind} {template.version_label} activa en {template.storage_key}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kind", choices=sorted(BUNDLED), default="om")
    parser.add_argument("--file", type=Path, help="Word a cargar (por defecto, la del código)")
    parser.add_argument("--version", required=True, help="Versión del formato, por ejemplo V02")
    parser.add_argument("--force", action="store_true", help="Reemplaza la plantilla activa")
    args = parser.parse_args()
    path = args.file or BUNDLED[args.kind]
    if not path.is_file():
        print(f"No existe el archivo {path}", file=sys.stderr)
        return 1
    return asyncio.run(_install(args.kind, path, args.version, args.force))


if __name__ == "__main__":
    sys.exit(main())
