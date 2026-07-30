"""add node handshake nonce

Revision ID: 8d826e5f3a54
Revises: 7c715d4e2f43
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "8d826e5f3a54"
down_revision: str | None = "7c715d4e2f43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_handshake_nonce",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("nonce_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["runtime_node.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("nonce_hash"),
    )
    op.create_index("ix_node_handshake_nonce_node_id", "node_handshake_nonce", ["node_id"])
    op.create_index("ix_node_handshake_nonce_nonce_hash", "node_handshake_nonce", ["nonce_hash"])
    op.create_index("ix_node_handshake_nonce_expires_at", "node_handshake_nonce", ["expires_at"])


def downgrade() -> None:
    op.drop_table("node_handshake_nonce")
