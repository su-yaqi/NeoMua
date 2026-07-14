"""add conversation configuration revisions

Revision ID: f7c3a9e1b204
Revises: e1b9c8d7f603
Create Date: 2026-07-13 20:30:00
"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f7c3a9e1b204"
down_revision = "e1b9c8d7f603"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_configuration_revision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "mode",
            postgresql.ENUM(
                "chat", "agent", name="conversationmode", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("provider_config_id", sa.Uuid(), nullable=True),
        sa.Column(
            "model_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("organizer_agent_id", sa.Uuid(), nullable=True),
        sa.Column(
            "participant_ids",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversation.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["provider_config_id"], ["llm_provider_config.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organizer_agent_id"], ["conversation_agent.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "revision",
            name="uq_conversation_configuration_revision",
        ),
    )
    op.create_index(
        "ix_conversation_configuration_revision_conversation_id",
        "conversation_configuration_revision",
        ["conversation_id"],
    )
    op.add_column(
        "conversation",
        sa.Column("current_configuration_revision_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversation_current_configuration_revision",
        "conversation",
        "conversation_configuration_revision",
        ["current_configuration_revision_id"],
        ["id"],
        ondelete="RESTRICT",
        use_alter=True,
    )
    op.add_column(
        "conversation_message",
        sa.Column("configuration_revision_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversation_message_configuration_revision",
        "conversation_message",
        "conversation_configuration_revision",
        ["configuration_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_conversation_message_configuration_revision",
        "conversation_message",
        type_="foreignkey",
    )
    op.drop_column("conversation_message", "configuration_revision_id")
    op.drop_constraint(
        "fk_conversation_current_configuration_revision",
        "conversation",
        type_="foreignkey",
    )
    op.drop_column("conversation", "current_configuration_revision_id")
    op.drop_index(
        "ix_conversation_configuration_revision_conversation_id",
        table_name="conversation_configuration_revision",
    )
    op.drop_table("conversation_configuration_revision")
