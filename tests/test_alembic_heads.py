"""The migrations form one line: the deploy runs ``alembic upgrade head``, which refuses to
choose between two heads (two branches that each added a migration on the same parent)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def test_there_is_a_single_migration_head() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))

    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, (
        f"Varias cabezas de migración {sorted(heads)}: renumera la más nueva y encadénala "
        "después de la otra (down_revision)"
    )
