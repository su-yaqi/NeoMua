"""close v0.9 review gaps

Revision ID: fc5a7b9d1e34
Revises: fb4e6f8a0b23
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "fc5a7b9d1e34"
down_revision: str | None = "fb4e6f8a0b23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    binding_columns = {
        column["name"] for column in inspector.get_columns("runtime_model_binding")
    }
    if "validated_capability_fingerprint" not in binding_columns:
        op.add_column(
            "runtime_model_binding",
            sa.Column(
                "validated_capability_fingerprint", sa.String(64), nullable=True
            ),
        )
    if "validation_expires_at" not in binding_columns:
        op.add_column(
            "runtime_model_binding",
            sa.Column(
                "validation_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
        )
    if not inspector.has_table("agent_task_model_call_usage"):
        op.create_table(
            "agent_task_model_call_usage",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("task_id", sa.Uuid(), nullable=False),
            sa.Column("runtime_model_binding_id", sa.Uuid(), nullable=False),
            sa.Column("call_sequence", sa.Integer(), nullable=False),
            sa.Column("event_sequence", sa.Integer(), nullable=False),
            sa.Column(
                "usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False
            ),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column(
                "error", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["task_id"], ["agent_task.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["runtime_model_binding_id"],
                ["runtime_model_binding.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "task_id", "call_sequence", name="uq_agent_task_model_call_usage"
            ),
        )
        op.create_index(
            "ix_agent_task_model_call_usage_task_id",
            "agent_task_model_call_usage",
            ["task_id"],
        )
    conversation_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("conversation_agent")
    }
    if "uq_conversation_agent" in conversation_constraints:
        op.drop_constraint(
            "uq_conversation_agent", "conversation_agent", type_="unique"
        )


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_conversation_agent",
        "conversation_agent",
        ["conversation_id", "agent_id"],
    )
    op.drop_index(
        "ix_agent_task_model_call_usage_task_id",
        table_name="agent_task_model_call_usage",
    )
    op.drop_table("agent_task_model_call_usage")
    op.drop_column("runtime_model_binding", "validation_expires_at")
    op.drop_column(
        "runtime_model_binding", "validated_capability_fingerprint"
    )
