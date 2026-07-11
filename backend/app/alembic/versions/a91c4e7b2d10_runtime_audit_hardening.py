"""runtime audit hardening

Revision ID: a91c4e7b2d10
Revises: 8d826e5f3a54
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a91c4e7b2d10"
down_revision: str | None = "8d826e5f3a54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_task", sa.Column("dispatch_connection_id", sa.Uuid()))
    op.add_column(
        "agent_task",
        sa.Column("dispatch_reserved_until", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_agent_task_dispatch_connection_id",
        "agent_task",
        ["dispatch_connection_id"],
    )
    op.create_index(
        "ix_agent_task_dispatch_reserved_until",
        "agent_task",
        ["dispatch_reserved_until"],
    )
    op.create_index(
        "ix_agent_task_dispatch_candidate",
        "agent_task",
        ["target_node_id", "status", "dispatch_reserved_until"],
    )

    op.add_column(
        "node_credential",
        sa.Column("replacement_issued_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "node_credential",
        sa.Column("replacement_grace_until", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_node_credential_replacement_grace_until",
        "node_credential",
        ["replacement_grace_until"],
    )

    op.add_column(
        "artifact_deployment",
        sa.Column(
            "logical_target",
            postgresql.ENUM(
                "agents",
                "skills",
                "mcp",
                "cli",
                "workspace",
                name="logicaltarget",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE artifact_deployment AS deployment
        SET logical_target = artifact.logical_target
        FROM runtime_artifact AS artifact
        WHERE deployment.artifact_id = artifact.id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM artifact_deployment WHERE logical_target IS NULL) THEN
            RAISE EXCEPTION 'artifact deployment logical_target backfill failed';
          END IF;
        END $$
        """
    )
    op.alter_column("artifact_deployment", "logical_target", nullable=False)
    op.add_column(
        "artifact_deployment", sa.Column("dispatch_connection_id", sa.Uuid())
    )
    op.add_column(
        "artifact_deployment",
        sa.Column("dispatch_reserved_until", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_artifact_deployment_dispatch_connection_id",
        "artifact_deployment",
        ["dispatch_connection_id"],
    )
    op.create_index(
        "ix_artifact_deployment_dispatch_reserved_until",
        "artifact_deployment",
        ["dispatch_reserved_until"],
    )
    op.create_index(
        "uq_artifact_deployment_active_target",
        "artifact_deployment",
        ["node_id", "logical_target"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'dispatched')"),
    )

    op.create_table(
        "refresh_session",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "token_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replaced_by_id", sa.Uuid()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"], ["refresh_session.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    for column in ("family_id", "user_id", "token_hash", "expires_at"):
        op.create_index(f"ix_refresh_session_{column}", "refresh_session", [column])


def downgrade() -> None:
    op.drop_table("refresh_session")
    op.drop_index(
        "uq_artifact_deployment_active_target", table_name="artifact_deployment"
    )
    op.drop_index(
        "ix_artifact_deployment_dispatch_reserved_until",
        table_name="artifact_deployment",
    )
    op.drop_index(
        "ix_artifact_deployment_dispatch_connection_id",
        table_name="artifact_deployment",
    )
    op.drop_column("artifact_deployment", "dispatch_reserved_until")
    op.drop_column("artifact_deployment", "dispatch_connection_id")
    op.drop_column("artifact_deployment", "logical_target")
    op.drop_index(
        "ix_node_credential_replacement_grace_until", table_name="node_credential"
    )
    op.drop_column("node_credential", "replacement_grace_until")
    op.drop_column("node_credential", "replacement_issued_at")
    op.drop_index("ix_agent_task_dispatch_candidate", table_name="agent_task")
    op.drop_index("ix_agent_task_dispatch_reserved_until", table_name="agent_task")
    op.drop_index("ix_agent_task_dispatch_connection_id", table_name="agent_task")
    op.drop_column("agent_task", "dispatch_reserved_until")
    op.drop_column("agent_task", "dispatch_connection_id")
