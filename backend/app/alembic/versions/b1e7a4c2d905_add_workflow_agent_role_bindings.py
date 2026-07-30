"""add workflow agent role bindings

Revision ID: b1e7a4c2d905
Revises: 08d4f1a6c9b2
Create Date: 2026-07-14 00:20:00
"""

import sqlalchemy as sa
from alembic import op

revision = "b1e7a4c2d905"
down_revision = "08d4f1a6c9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_node_definition",
        sa.Column("agent_role_key", sa.String(length=128), nullable=True),
    )
    op.create_table(
        "workflow_instance_agent_binding",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_instance_id", sa.Uuid(), nullable=False),
        sa.Column("role_key", sa.String(length=128), nullable=False),
        sa.Column("runtime_profile_id", sa.Uuid(), nullable=False),
        sa.Column("agent_release_id", sa.Uuid(), nullable=False),
        sa.Column("resolved_spec_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_instance_id"], ["workflow_instance.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["runtime_profile_id"], ["runtime_profile.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["agent_release_id"], ["agent_release.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_instance_id",
            "role_key",
            name="uq_workflow_instance_agent_role",
        ),
    )
    op.create_index(
        "ix_workflow_instance_agent_binding_workflow_instance_id",
        "workflow_instance_agent_binding",
        ["workflow_instance_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_instance_agent_binding_workflow_instance_id",
        table_name="workflow_instance_agent_binding",
    )
    op.drop_table("workflow_instance_agent_binding")
    op.drop_column("workflow_node_definition", "agent_role_key")
