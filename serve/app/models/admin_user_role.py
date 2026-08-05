from sqlalchemy import Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class AdminUserRole(Base):
    __tablename__ = "admin_user_roles"

    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)

    user = relationship("AdminUser", back_populates="roles_assoc")
    role = relationship("Role", back_populates="users_assoc")
