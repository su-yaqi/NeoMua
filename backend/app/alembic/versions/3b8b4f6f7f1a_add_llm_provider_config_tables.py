"""Add llm provider config tables

Revision ID: 3b8b4f6f7f1a
Revises: 9c0a54914c78
Create Date: 2026-06-11 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3b8b4f6f7f1a"
down_revision: str | None = "8f3a7d1c2b4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    provider_auth_type = postgresql.ENUM(
        "api_key",
        "oauth_external",
        "oauth_device_code",
        "aws_sdk",
        "external_process",
        "copilot_token",
        "custom",
        name="providerauthtype", create_type=False,
    )
    provider_validation_status = postgresql.ENUM(
        "unverified",
        "success",
        "failed",
        "unsupported",
        name="providervalidationstatus", create_type=False,
    )
    provider_model_source_type = postgresql.ENUM(
        "discovered",
        "manual",
        name="providermodelsourcetype", create_type=False,
    )
    provider_model_sync_status = postgresql.ENUM(
        "active",
        "stale",
        "sync_failed",
        name="providermodelsyncstatus", create_type=False,
    )

    bind = op.get_bind()
    provider_auth_type.create(bind, checkfirst=True)
    provider_validation_status.create(bind, checkfirst=True)
    provider_model_source_type.create(bind, checkfirst=True)
    provider_model_sync_status.create(bind, checkfirst=True)

    op.create_table(
        "llm_provider_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("config_name", sa.String(length=255), nullable=False),
        sa.Column("provider_slug", sa.String(length=128), nullable=False),
        sa.Column("provider_display_name", sa.String(length=255), nullable=False),
        sa.Column("auth_type", provider_auth_type, nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("secret_masked", sa.String(length=255), nullable=True),
        sa.Column("extra_config", sa.JSON(), nullable=False),
        sa.Column("supports_health_check", sa.Boolean(), nullable=False),
        sa.Column("supports_model_discovery", sa.Boolean(), nullable=False),
        sa.Column("validation_status", provider_validation_status, nullable=False),
        sa.Column("validation_message", sa.String(length=1024), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "config_name"),
    )

    op.create_table(
        "llm_provider_model",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("source_type", provider_model_source_type, nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("sync_status", provider_model_sync_status, nullable=False),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["provider_config_id"], ["llm_provider_config.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_config_id", "model_id"),
    )


def downgrade() -> None:
    op.drop_table("llm_provider_model")
    op.drop_table("llm_provider_config")

    bind = op.get_bind()
    sa.Enum(name="providermodelsyncstatus").drop(bind, checkfirst=True)
    sa.Enum(name="providermodelsourcetype").drop(bind, checkfirst=True)
    sa.Enum(name="providervalidationstatus").drop(bind, checkfirst=True)
    sa.Enum(name="providerauthtype").drop(bind, checkfirst=True)
