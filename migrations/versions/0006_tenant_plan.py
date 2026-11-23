"""Assign plans to existing tenants and default new tenants to free."""

from alembic import op
import sqlalchemy as sa

revision = "0006_tenant_plan"
down_revision = "0005_index_dispatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("plan", sa.String(32), nullable=False, server_default="free"))
    op.execute("UPDATE tenants SET plan = CASE WHEN id = 'tenant_demo' THEN 'pro' ELSE 'standard' END")
    op.create_check_constraint("ck_tenant_plan", "tenants", "plan IN ('free','standard','pro')")


def downgrade() -> None:
    op.drop_constraint("ck_tenant_plan", "tenants", type_="check")
    op.drop_column("tenants", "plan")
