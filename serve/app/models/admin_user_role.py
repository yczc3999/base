from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class AdminUserRole(Base):
    __tablename__ = "admin_user_roles"

    admin_user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(Integer, primary_key=True)
