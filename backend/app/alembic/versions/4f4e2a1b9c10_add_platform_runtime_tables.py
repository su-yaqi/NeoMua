"""add platform runtime tables

Revision ID: 4f4e2a1b9c10
Revises: 3b8b4f6f7f1a
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "4f4e2a1b9c10"
down_revision: str | None = "3b8b4f6f7f1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    runtime_type = sa.Enum("platform", "node", name="runtimetype")
    route_mode = sa.Enum("platform_gateway", "direct_anthropic", name="runtimeroutemode")
    task_status = sa.Enum("queued", "dispatched", "running", "cancelling", "succeeded", "failed", "cancelled", "interrupted", "rejected", name="agenttaskstatus")
    event_type = sa.Enum("user_message", "assistant_message", "tool_call", "tool_result", "status", "error", "result", name="agenteventtype")
    runtime_type.create(op.get_bind(), checkfirst=True)
    route_mode.create(op.get_bind(), checkfirst=True)
    task_status.create(op.get_bind(), checkfirst=True)
    event_type.create(op.get_bind(), checkfirst=True)
    op.create_table("runtime_profile",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_type", runtime_type, nullable=False), sa.Column("route_mode", route_mode, nullable=False),
        sa.Column("provider_config_id", sa.Uuid(), nullable=True), sa.Column("model_id", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("base_url", sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=True), sa.Column("permission_mode", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["provider_config_id"], ["llm_provider_config.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("namespace_id", "runtime_type", name="uq_runtime_profile_namespace_platform"))
    op.create_table("runtime_secret", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("namespace_id", sa.Uuid(), nullable=False), sa.Column("runtime_profile_id", sa.Uuid(), nullable=False), sa.Column("secret_ciphertext", sa.Text(), nullable=False), sa.Column("secret_masked", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True), sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["runtime_profile_id"], ["runtime_profile.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("runtime_profile_id"))
    op.create_table("agent_session", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("namespace_id", sa.Uuid(), nullable=False), sa.Column("runtime_profile_id", sa.Uuid(), nullable=False), sa.Column("sdk_session_id", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True), sa.Column("created_by", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["runtime_profile_id"], ["runtime_profile.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["created_by"], ["user.id"]), sa.PrimaryKeyConstraint("id"))
    op.create_table("agent_task", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("namespace_id", sa.Uuid(), nullable=False), sa.Column("session_id", sa.Uuid(), nullable=True), sa.Column("runtime_profile_id", sa.Uuid(), nullable=False), sa.Column("status", task_status, nullable=False), sa.Column("prompt", sa.Text(), nullable=False), sa.Column("snapshot", sa.JSON(), nullable=False), sa.Column("final_result", sa.JSON(), nullable=True), sa.Column("created_by", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["runtime_profile_id"], ["runtime_profile.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["session_id"], ["agent_session.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["created_by"], ["user.id"]), sa.PrimaryKeyConstraint("id"))
    op.create_table("agent_event", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("namespace_id", sa.Uuid(), nullable=False), sa.Column("task_id", sa.Uuid(), nullable=False), sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("event_type", event_type, nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["task_id"], ["agent_task.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("task_id", "sequence", name="uq_agent_event_task_sequence"))


def downgrade() -> None:
    for table in ("agent_event", "agent_task", "agent_session", "runtime_secret", "runtime_profile"):
        op.drop_table(table)
    for name in ("agenteventtype", "agenttaskstatus", "runtimeroutemode", "runtimetype"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
