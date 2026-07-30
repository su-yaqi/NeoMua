"""add agent task execution fields

Revision ID: 6b604c3d1e32
Revises: 5a5f3b2c0d21
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6b604c3d1e32"
down_revision: str | None = "5a5f3b2c0d21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    task_kind = postgresql.ENUM(
        "ordinary", "admin", name="agenttaskkind", create_type=False
    )
    task_kind.create(op.get_bind(), checkfirst=True)
    op.add_column("agent_task", sa.Column("target_node_id", sa.Uuid(), nullable=True))
    op.add_column(
        "agent_task",
        sa.Column("task_kind", task_kind, nullable=False, server_default="ordinary"),
    )
    op.add_column(
        "agent_task",
        sa.Column("idempotency_key", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
    )
    op.add_column(
        "agent_task",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column(
        "agent_task", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_agent_task_target_node_id", "agent_task", "runtime_node",
        ["target_node_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_agent_task_target_node_id", "agent_task", ["target_node_id"])
    op.create_unique_constraint(
        "uq_agent_task_namespace_idempotency",
        "agent_task", ["namespace_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_agent_task_namespace_idempotency", "agent_task", type_="unique")
    op.drop_index("ix_agent_task_target_node_id", table_name="agent_task")
    op.drop_constraint("fk_agent_task_target_node_id", "agent_task", type_="foreignkey")
    for column in ("completed_at", "updated_at", "idempotency_key", "task_kind", "target_node_id"):
        op.drop_column("agent_task", column)
    sa.Enum(name="agenttaskkind").drop(op.get_bind(), checkfirst=True)
