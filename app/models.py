from uuid import UUID, uuid4

from sqlalchemy import ARRAY, Index, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(320))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    social_links: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        default=list,
        server_default=text("'{}'::text[]"),
    )

    __table_args__ = (
        Index("uq_clients_email_lower", func.lower(email), unique=True),
    )
