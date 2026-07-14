"""add standalone workflow context

Revision ID: 08d4f1a6c9b2
Revises: f7c3a9e1b204
Create Date: 2026-07-13 23:45:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "08d4f1a6c9b2"
down_revision = "f7c3a9e1b204"
branch_labels = None
depends_on = None


workflow_context_mode = postgresql.ENUM(
    "project",
    "standalone",
    name="workflowcontextmode",
)


def upgrade() -> None:
    workflow_context_mode.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "workflow_instance",
        sa.Column(
            "context_mode",
            workflow_context_mode,
            nullable=False,
            server_default="project",
        ),
    )
    op.alter_column(
        "workflow_instance",
        "project_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.alter_column(
        "workflow_instance",
        "context_mode",
        server_default=None,
    )


def downgrade() -> None:
    standalone_count = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM workflow_instance "
            "WHERE project_id IS NULL OR context_mode = 'standalone'"
        )
    ).scalar_one()
    if standalone_count:
        raise RuntimeError(
            "Cannot downgrade while standalone Workflow instances exist"
        )
    op.alter_column(
        "workflow_instance",
        "project_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_column("workflow_instance", "context_mode")
    workflow_context_mode.drop(op.get_bind(), checkfirst=True)
