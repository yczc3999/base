from sqlalchemy import Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class RoleMenu(Base):
    __tablename__ = "role_menus"

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    menu_id: Mapped[int] = mapped_column(ForeignKey("menus.id", ondelete="CASCADE"), primary_key=True)

    role = relationship("Role", back_populates="menus_assoc")
    menu = relationship("Menu", back_populates="roles_assoc")
