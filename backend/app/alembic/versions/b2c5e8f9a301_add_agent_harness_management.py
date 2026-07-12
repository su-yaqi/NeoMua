"""add agent harness management

Revision ID: b2c5e8f9a301
Revises: a91c4e7b2d10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c5e8f9a301"
down_revision: str | None = "a91c4e7b2d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    agent_status = postgresql.ENUM(
        "active", "archived", name="agentstatus", create_type=False
    )
    bind = op.get_bind()
    agent_status.create(bind, checkfirst=True)

    op.create_table(
        "agent_definition",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", agent_status, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["namespace_id"], ["namespace.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "namespace_id", "slug", name="uq_agent_definition_namespace_slug"
        ),
    )
    op.create_index(
        "ix_agent_definition_namespace_id", "agent_definition", ["namespace_id"]
    )

    op.create_table(
        "harness_profile",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("harness_type", sa.String(length=64), nullable=False),
        sa.Column("config_schema_version", sa.String(length=32), nullable=False),
        sa.Column("cli_version_constraint", sa.String(length=128), nullable=False),
        sa.Column("sdk_version_constraint", sa.String(length=128), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["namespace_id"], ["namespace.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "namespace_id", "name", name="uq_harness_profile_namespace_name"
        ),
    )
    op.create_index(
        "ix_harness_profile_namespace_id", "harness_profile", ["namespace_id"]
    )

    op.create_table(
        "agent_draft",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("harness_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_config_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validated_revision", sa.Integer(), nullable=True),
        sa.Column("validation_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agent_definition.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["harness_profile_id"], ["harness_profile.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["provider_config_id"], ["llm_provider_config.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("agent_id", name="uq_agent_draft_agent"),
    )


def downgrade() -> None:
    op.drop_table("agent_draft")
    op.drop_table("harness_profile")
    op.drop_index("ix_harness_profile_namespace_id", table_name="harness_profile")
    op.drop_table("agent_definition")
    op.drop_index("ix_agent_definition_namespace_id", table_name="agent_definition")
    agent_status = postgresql.ENUM(
        "active", "archived", name="agentstatus", create_type=False
    )
    agent_status.drop(op.get_bind(), checkfirst=True)
