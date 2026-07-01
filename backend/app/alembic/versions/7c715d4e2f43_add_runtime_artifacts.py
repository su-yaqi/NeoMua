"""add runtime artifacts

Revision ID: 7c715d4e2f43
Revises: 6b604c3d1e32
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c715d4e2f43"
down_revision: str | None = "6b604c3d1e32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    artifact_kind = postgresql.ENUM("agent", "skill", "mcp", "cli_config", "workspace_content", name="artifactkind", create_type=False)
    logical_target = postgresql.ENUM("agents", "skills", "mcp", "cli", "workspace", name="logicaltarget", create_type=False)
    deployment_status = postgresql.ENUM("pending", "dispatched", "applied", "failed", "expired", name="deploymentstatus", create_type=False)
    for enum in (artifact_kind, logical_target, deployment_status):
        enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "runtime_artifact",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("kind", artifact_kind, nullable=False), sa.Column("logical_target", logical_target, nullable=False),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("content_sha256", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("storage_key", sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False), sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False), sa.Column("signing_public_key", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "kind", "logical_target", "version", name="uq_runtime_artifact_namespace_version"),
    )
    op.create_index("ix_runtime_artifact_namespace_id", "runtime_artifact", ["namespace_id"])
    op.create_index("ix_runtime_artifact_content_sha256", "runtime_artifact", ["content_sha256"])
    op.create_table(
        "artifact_release",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False), sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rollback_of_release_id", sa.Uuid(), nullable=True), sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["runtime_artifact.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rollback_of_release_id"], ["artifact_release.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifact_release_namespace_id", "artifact_release", ["namespace_id"])
    op.create_table(
        "artifact_deployment",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("release_id", sa.Uuid(), nullable=False), sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False), sa.Column("previous_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False), sa.Column("status", deployment_status, nullable=False),
        sa.Column("error", sa.JSON(), nullable=True), sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["artifact_release.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["runtime_node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["runtime_artifact.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_artifact_id"], ["runtime_artifact.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("release_id", "node_id", "attempt", name="uq_artifact_deployment_attempt"),
    )
    for name, column in (("namespace_id", "namespace_id"), ("release_id", "release_id"), ("node_id", "node_id")):
        op.create_index(f"ix_artifact_deployment_{name}", "artifact_deployment", [column])
    op.create_table(
        "runtime_node_artifact",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("logical_target", logical_target, nullable=False), sa.Column("current_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("previous_artifact_id", sa.Uuid(), nullable=True), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["runtime_node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["current_artifact_id"], ["runtime_artifact.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["previous_artifact_id"], ["runtime_artifact.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("node_id", "logical_target", name="uq_node_artifact_target"),
    )
    op.create_index("ix_runtime_node_artifact_node_id", "runtime_node_artifact", ["node_id"])


def downgrade() -> None:
    for table in ("runtime_node_artifact", "artifact_deployment", "artifact_release", "runtime_artifact"):
        op.drop_table(table)
    for name in ("deploymentstatus", "logicaltarget", "artifactkind"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
