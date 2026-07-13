"""add conversation event stream

Revision ID: d0a8b7c6e502
Revises: c9d7e6f5a401
Create Date: 2026-07-13 17:18:00
"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d0a8b7c6e502"
down_revision = "c9d7e6f5a401"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "event_type",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "payload",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversation.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_conversation_event_sequence"
        ),
    )
    op.create_index(
        "ix_conversation_event_conversation_id",
        "conversation_event",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversation_event_cursor",
        "conversation_event",
        ["conversation_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_event_cursor", table_name="conversation_event")
    op.drop_index(
        "ix_conversation_event_conversation_id", table_name="conversation_event"
    )
    op.drop_table("conversation_event")
