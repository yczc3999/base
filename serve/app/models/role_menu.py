from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class RoleMenu(Base):
    __tablename__ = "role_menus"

    role_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    menu_id: Mapped[int] = mapped_column(Integer, primary_key=True)
