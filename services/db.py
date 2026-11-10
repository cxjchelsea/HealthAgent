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

class ConversationTask(Base):
    __tablename__ = "conversation_tasks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class UserMemory(Base):
    __tablename__ = "user_memories"
    __table_args__ = (UniqueConstraint("owner_id", "key", name="uq_user_memory_owner_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    key: Mapped[str] = mapped_column(String(80))
    value: Mapped[str] = mapped_column(String(500))

class ConversationPlan(Base):
    __tablename__ = "conversation_plans"
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), primary_key=True)
    snapshot: Mapped[str] = mapped_column(Text, default="{}")


class PendingWriteAuthority(Base):
    """Host-minted single-use authority for a clarified, internal task write."""
    __tablename__ = "pending_write_authorities"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    origin_turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id"), unique=True)
    origin_user_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    tool_name: Mapped[str] = mapped_column(String(80))
    source_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    consumed_turn_id: Mapped[str | None] = mapped_column(ForeignKey("turns.id"), nullable=True)
    candidate_turn_id: Mapped[str | None] = mapped_column(ForeignKey("turns.id"), nullable=True)
    candidate_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    candidate_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmation_nonce: Mapped[str | None] = mapped_column(String(40), nullable=True)
    candidate_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlanExecutionClaim(Base):
    """Durable per-conversation reservation for saved-plan tool execution."""
    __tablename__ = "plan_execution_claims"
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id"), unique=True)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class DependencyWorkerFence(Base):
    """Monotonic generation fence for one conversation's dependency-plan runner.

    A claim/Turn identifies the execution. This row additionally prevents an
    old worker from committing after an operator fenced that execution.
    No time-based lease is used as proof of worker termination.
    """
    __tablename__ = "dependency_worker_fences"
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id"))
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ConversationGoalState(Base):
    """Versioned, owner-scoped via parent conversation, verified by caller."""
    __tablename__ = "conversation_goal_states"
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    goal_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_json: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_steps: Mapped[int] = mapped_column(Integer, default=0)


class PrimaryRouteEvidence(Base):
    """Write-once Host admission evidence for one Primary Turn.

    Never stores prompts, user text, model raw output, tool arguments or
    credentials. The row is committed before any Primary-authorized effect.
    """
    __tablename__ = "primary_route_evidence"
    turn_id: Mapped[str] = mapped_column(
        ForeignKey("turns.id"), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True)
    proposal_id: Mapped[str] = mapped_column(String(40), unique=True)
    schema_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    route_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    semantic_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    preturn_snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(20))
    admission_reason: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now)


class PlanToolDispatch(Base):
    """Durable pre-dispatch intention. An unreceipted dispatch is never retryable."""
    __tablename__ = "plan_tool_dispatches"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id"), index=True)
    step_id: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(80))
    arguments_json: Mapped[str] = mapped_column(Text)
    tool_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="dispatch_unknown")
    receipt_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("turn_id", "step_id", name="uq_plan_dispatch_turn_step"),)


class PlanRecoveryAudit(Base):
    """Immutable recovery operator attestations (no credentials)."""
    __tablename__ = "plan_recovery_audits"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    turn_id: Mapped[str] = mapped_column(String(40))
    operator_id: Mapped[str] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(48))
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AgentTaskState(Base):
    """CAS-protected projection of one active Agent task per conversation."""
    __tablename__ = "agent_task_states"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    plan_revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24))
    step_states: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AgentStateTransition(Base):
    """Append-only state change evidence; not authority to execute tools."""
    __tablename__ = "agent_state_transitions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    entity_type: Mapped[str] = mapped_column(String(16))
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_state: Mapped[str] = mapped_column(String(24))
    reason_code: Mapped[str] = mapped_column(String(40))
    attempt_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AgentAttempt(Base):
    """Durable bounded record of a real tool invocation, not a model claim."""
    __tablename__ = "agent_attempts"
    __table_args__ = (UniqueConstraint("turn_id", "id", name="uq_agent_attempt_turn_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(40))
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_name: Mapped[str] = mapped_column(String(80))
    arguments_digest: Mapped[str] = mapped_column(String(64))
    tool_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result_projection: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

def engine_for(url):
    if url == "sqlite://":
        return create_engine(url, connect_args={"check_same_thread":False}, poolclass=StaticPool)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread":False})
    return create_engine(url, pool_pre_ping=True)

def database(url):
    engine=engine_for(url)
    return engine, sessionmaker(engine, expire_on_commit=False)
