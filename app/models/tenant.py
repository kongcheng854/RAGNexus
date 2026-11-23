from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt


class Tenant(CreatedAt, Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint("status IN ('active', 'disabled')", name="ck_tenant_status"),
                      CheckConstraint("plan IN ('free', 'standard', 'pro')", name="ck_tenant_plan"))

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    plan: Mapped[str] = mapped_column(String(32), server_default="free", nullable=False)
    status: Mapped[str] = mapped_column(String(16), server_default="active")
