"""add workflow attachments

Revision ID: e1b9c8d7f603
Revises: d0a8b7c6e502
Create Date: 2026-07-13 17:34:00
"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e1b9c8d7f603"
down_revision = "d0a8b7c6e502"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_attachment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_instance_id", sa.Uuid(), nullable=False),
        sa.Column(
            "filename", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False
        ),
        sa.Column(
            "content_type",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column(
            "content_digest",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "storage_ref",
            sqlmodel.sql.sqltypes.AutoString(length=1024),
            nullable=False,
        ),
        sa.Column(
            "scan_status",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
        ),
        sa.Column(
            "scan_details",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workflow_instance_id"],
            ["workflow_instance.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_instance_id",
            "content_digest",
            name="uq_workflow_attachment_content",
        ),
    )
    op.create_index(
        "ix_workflow_attachment_workflow_instance_id",
        "workflow_attachment",
        ["workflow_instance_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_attachment_workflow_instance_id",
        table_name="workflow_attachment",
    )
    op.drop_table("workflow_attachment")
