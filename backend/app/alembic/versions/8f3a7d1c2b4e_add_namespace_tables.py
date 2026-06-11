"""Add namespace and user_namespace_link tables

Revision ID: 8f3a7d1c2b4e
Revises: fe56fa70289e
Create Date: 2026-05-20 22:10:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "8f3a7d1c2b4e"
down_revision = "fe56fa70289e"
branch_labels = None
depends_on = None


def upgrade():
    namespace_role_enum = sa.Enum(
        "admin", "developer", "user", name="namespacerole"
    )
    namespace_role_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "namespace",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_namespace_name", "namespace", ["name"], unique=True)
    op.create_index("ix_namespace_code", "namespace", ["code"], unique=True)

    op.create_table(
        "user_namespace_link",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            namespace_role_enum,
            nullable=False,
            server_default="user",
        ),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "namespace_id"),
    )


def downgrade():
    op.drop_table("user_namespace_link")
    op.drop_index("ix_namespace_code", table_name="namespace")
    op.drop_index("ix_namespace_name", table_name="namespace")
    op.drop_table("namespace")

    namespace_role_enum = sa.Enum(
        "admin", "developer", "user", name="namespacerole"
    )
    namespace_role_enum.drop(op.get_bind(), checkfirst=True)
