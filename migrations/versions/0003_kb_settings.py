"""Store per-knowledge-base query settings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_kb_settings"
down_revision = "0002_tenant_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_bases", sa.Column("settings", JSONB(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("knowledge_bases", "settings")
