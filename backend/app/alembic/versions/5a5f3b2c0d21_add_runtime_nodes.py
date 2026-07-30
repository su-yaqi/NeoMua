"""add runtime nodes

Revision ID: 5a5f3b2c0d21
Revises: 4f4e2a1b9c10
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "5a5f3b2c0d21"
down_revision: str | None = "4f4e2a1b9c10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_runtime_profile_namespace_platform", "runtime_profile", type_="unique"
    )
    op.create_index(
        "uq_runtime_profile_namespace_platform",
        "runtime_profile",
        ["namespace_id"],
        unique=True,
        postgresql_where=sa.text("runtime_type = 'platform'"),
    )
    op.create_table(
        "runtime_node",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_profile_id", sa.Uuid(), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("hostname", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("os_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("architecture", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("agent_version", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("sdk_version", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("key_fingerprint", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connection_id", sa.Uuid(), nullable=True),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["runtime_profile_id"], ["runtime_profile.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("runtime_profile_id"),
        sa.UniqueConstraint("key_fingerprint"),
    )
    op.create_index("ix_runtime_node_namespace_id", "runtime_node", ["namespace_id"])
    op.create_index("ix_runtime_node_key_fingerprint", "runtime_node", ["key_fingerprint"])
    op.create_index("ix_runtime_node_connection_id", "runtime_node", ["connection_id"])
    op.create_table(
        "node_enrollment_token",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_node_enrollment_token_namespace_id", "node_enrollment_token", ["namespace_id"])
    op.create_index("ix_node_enrollment_token_token_hash", "node_enrollment_token", ["token_hash"])
    op.create_table(
        "node_credential",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("key_fingerprint", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["runtime_node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["replaced_by_id"], ["node_credential.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_node_credential_node_id", "node_credential", ["node_id"])
    op.create_index("ix_node_credential_namespace_id", "node_credential", ["namespace_id"])


def downgrade() -> None:
    op.drop_table("node_credential")
    op.drop_table("node_enrollment_token")
    op.drop_table("runtime_node")
    bind = op.get_bind()
    constraints = {
        item["name"] for item in sa.inspect(bind).get_unique_constraints("runtime_profile")
    }
    if "uq_runtime_profile_namespace_platform" not in constraints:
        op.drop_index(
            "uq_runtime_profile_namespace_platform", table_name="runtime_profile"
        )
        op.create_unique_constraint(
            "uq_runtime_profile_namespace_platform",
            "runtime_profile",
            ["namespace_id", "runtime_type"],
        )
