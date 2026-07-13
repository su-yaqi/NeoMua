"""add runtime harness capabilities

Revision ID: c4f6a8d2e913
Revises: b2c5e8f9a301
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4f6a8d2e913"
down_revision: str | None = "b2c5e8f9a301"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_profile",
        sa.Column(
            "harness_capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "runtime_node",
        sa.Column(
            "harness_capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("runtime_profile", "harness_capabilities", server_default=None)
    op.alter_column("runtime_node", "harness_capabilities", server_default=None)


def downgrade() -> None:
    op.drop_column("runtime_node", "harness_capabilities")
    op.drop_column("runtime_profile", "harness_capabilities")
