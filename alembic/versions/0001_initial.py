"""Initial empty revision for the modular application."""

from collections.abc import Sequence

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial migration marker; modules add their own tables later."""

    pass


def downgrade() -> None:
    """Remove the initial migration marker."""

    pass
