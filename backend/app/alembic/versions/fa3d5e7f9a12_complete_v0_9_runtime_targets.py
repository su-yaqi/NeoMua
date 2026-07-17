"""complete v0.9 Runtime targets and execution records

Revision ID: fa3d5e7f9a12
Revises: f9b2c4d6e810
Create Date: 2026-07-17 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "fa3d5e7f9a12"
down_revision = "f9b2c4d6e810"
branch_labels = None
depends_on = None


def _add_runtime_target(
    table: str,
    *,
    legacy_column: str,
    new_column: str = "runtime_instance_id",
    ondelete: str = "RESTRICT",
    make_legacy_nullable: bool = True,
) -> None:
    if make_legacy_nullable:
        op.alter_column(table, legacy_column, existing_type=sa.Uuid(), nullable=True)
    op.add_column(table, sa.Column(new_column, sa.Uuid(), nullable=True))
    op.create_index(f"ix_{table}_{new_column}", table, [new_column])
    op.create_foreign_key(
        f"fk_{table}_{new_column}",
        table,
        "runtime_instance",
        [new_column],
        ["id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    op.execute(
        "ALTER TYPE runtimeconfigurationstatus ADD VALUE IF NOT EXISTS 'applying'"
    )
    op.add_column("runtime_node", sa.Column("discovery_digest", sa.String(64)))
    op.alter_column("conversation", "runtime_id", existing_type=sa.Uuid(), nullable=True)

    _add_runtime_target("runtime_job", legacy_column="runtime_profile_id")
    _add_runtime_target(
        "workflow_instance_agent_binding", legacy_column="runtime_profile_id"
    )
    _add_runtime_target(
        "workflow_node_instance",
        legacy_column="resolved_runtime_id",
        new_column="resolved_runtime_instance_id",
    )
    _add_runtime_target(
        "workflow_node_execution",
        legacy_column="runtime_id",
    )

    _add_runtime_target("runtime_skill_state", legacy_column="runtime_profile_id")
    op.create_index(
        "uq_runtime_skill_state_v09",
        "runtime_skill_state",
        ["runtime_instance_id", "skill_id"],
        unique=True,
        postgresql_where=sa.text("runtime_instance_id IS NOT NULL"),
        sqlite_where=sa.text("runtime_instance_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_runtime_skill_state_one_target",
        "runtime_skill_state",
        "(runtime_profile_id IS NULL) <> (runtime_instance_id IS NULL)",
    )

    _add_runtime_target("mcp_target_binding", legacy_column="runtime_profile_id")
    op.create_index(
        "uq_mcp_target_v09",
        "mcp_target_binding",
        ["revision_id", "runtime_instance_id"],
        unique=True,
        postgresql_where=sa.text("runtime_instance_id IS NOT NULL"),
        sqlite_where=sa.text("runtime_instance_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_mcp_target_binding_one_target",
        "mcp_target_binding",
        "(runtime_profile_id IS NULL) <> (runtime_instance_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_mcp_target_binding_one_target", "mcp_target_binding", type_="check"
    )
    op.drop_index("uq_mcp_target_v09", table_name="mcp_target_binding")
    op.drop_constraint(
        "fk_mcp_target_binding_runtime_instance_id",
        "mcp_target_binding",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_mcp_target_binding_runtime_instance_id", table_name="mcp_target_binding"
    )
    op.drop_column("mcp_target_binding", "runtime_instance_id")
    op.alter_column(
        "mcp_target_binding", "runtime_profile_id", existing_type=sa.Uuid(), nullable=False
    )

    op.drop_constraint(
        "ck_runtime_skill_state_one_target", "runtime_skill_state", type_="check"
    )
    op.drop_index("uq_runtime_skill_state_v09", table_name="runtime_skill_state")
    op.drop_constraint(
        "fk_runtime_skill_state_runtime_instance_id",
        "runtime_skill_state",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_runtime_skill_state_runtime_instance_id", table_name="runtime_skill_state"
    )
    op.drop_column("runtime_skill_state", "runtime_instance_id")
    op.alter_column(
        "runtime_skill_state", "runtime_profile_id", existing_type=sa.Uuid(), nullable=False
    )

    for table, legacy_column, new_column in [
        ("workflow_node_execution", "runtime_id", "runtime_instance_id"),
        (
            "workflow_node_instance",
            "resolved_runtime_id",
            "resolved_runtime_instance_id",
        ),
        (
            "workflow_instance_agent_binding",
            "runtime_profile_id",
            "runtime_instance_id",
        ),
        ("runtime_job", "runtime_profile_id", "runtime_instance_id"),
    ]:
        op.drop_constraint(
            f"fk_{table}_{new_column}", table, type_="foreignkey"
        )
        op.drop_index(f"ix_{table}_{new_column}", table_name=table)
        op.drop_column(table, new_column)
        op.alter_column(table, legacy_column, existing_type=sa.Uuid(), nullable=False)

    op.alter_column("conversation", "runtime_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("runtime_node", "discovery_digest")
    op.execute(
        "UPDATE runtime_configuration_revision SET status = 'failed' WHERE status = 'applying'"
    )
    op.execute("ALTER TYPE runtimeconfigurationstatus RENAME TO runtimeconfigurationstatus_old")
    op.execute("CREATE TYPE runtimeconfigurationstatus AS ENUM ('desired', 'applied', 'failed')")
    op.execute(
        "ALTER TABLE runtime_configuration_revision ALTER COLUMN status TYPE "
        "runtimeconfigurationstatus USING status::text::runtimeconfigurationstatus"
    )
    op.execute("DROP TYPE runtimeconfigurationstatus_old")
