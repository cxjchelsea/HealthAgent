import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, ForeignKey, Text, Integer, DateTime, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

def now():
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")

class Turn(Base):
    __tablename__="turns"
    __table_args__=(UniqueConstraint("conversation_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(20), default="running")
    assistant_message_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    input_chars: Mapped[int] = mapped_column(Integer, default=0)
    output_chars: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

def engine_for(url):
    if url == "sqlite://":
        return create_engine(url, connect_args={"check_same_thread":False}, poolclass=StaticPool)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread":False})
    return create_engine(url, pool_pre_ping=True)

def database(url):
    engine=engine_for(url)
    return engine, sessionmaker(engine, expire_on_commit=False)
