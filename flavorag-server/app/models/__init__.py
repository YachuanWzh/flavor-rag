import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, SmallInteger, Text, BigInteger, DateTime, JSON
from sqlalchemy.orm import DeclarativeBase, declared_attr


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def gen_id() -> str:
    return uuid.uuid4().hex[:16]


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    create_time = Column(DateTime, default=_utcnow)
    update_time = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    deleted = Column(SmallInteger, default=0)


class User(Base, TimestampMixin):
    __tablename__ = "t_user"

    id = Column(String(20), primary_key=True, default=gen_id)
    username = Column(String(64), nullable=False, unique=True)
    password = Column(String(128), nullable=False)
    role = Column(String(32), nullable=False, default="user")
    avatar = Column(String(128))


class Conversation(Base, TimestampMixin):
    __tablename__ = "t_conversation"

    id = Column(String(20), primary_key=True, default=gen_id)
    conversation_id = Column(String(20), nullable=False)
    user_id = Column(String(20), nullable=False)
    title = Column(String(128), nullable=False, default="新对话")
    last_time = Column(DateTime)


class Message(Base, TimestampMixin):
    __tablename__ = "t_message"

    id = Column(String(20), primary_key=True, default=gen_id)
    conversation_id = Column(String(20), nullable=False)
    user_id = Column(String(20), nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    thinking_content = Column(Text)
    thinking_duration = Column(Integer)
    sources = Column(JSON)
    recommended_questions = Column(JSON)
    message_status = Column(String(16), default="NORMAL")


class MessageFeedback(Base, TimestampMixin):
    __tablename__ = "t_message_feedback"

    id = Column(String(20), primary_key=True, default=gen_id)
    message_id = Column(String(20), nullable=False)
    conversation_id = Column(String(20), nullable=False)
    user_id = Column(String(20), nullable=False)
    vote = Column(SmallInteger, nullable=False)
    reason = Column(String(255))
    comment = Column(String(1024))


class KnowledgeBase(Base, TimestampMixin):
    __tablename__ = "t_knowledge_base"

    id = Column(String(20), primary_key=True, default=gen_id)
    name = Column(String(128), nullable=False)
    embedding_model = Column(String(64), nullable=False)
    collection_name = Column(String(64), nullable=False, unique=True)
    created_by = Column(String(20), nullable=False)
    updated_by = Column(String(20))


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "t_knowledge_document"

    id = Column(String(20), primary_key=True, default=gen_id)
    kb_id = Column(String(20), nullable=False)
    doc_name = Column(String(256), nullable=False)
    enabled = Column(SmallInteger, default=1)
    chunk_count = Column(Integer, default=0)
    file_url = Column(String(1024), nullable=False)
    file_type = Column(String(16), nullable=False)
    file_size = Column(BigInteger)
    process_mode = Column(String(16), default="chunk")
    status = Column(String(16), default="pending")
    source_type = Column(String(16))
    source_location = Column(String(1024))
    schedule_enabled = Column(SmallInteger)
    schedule_cron = Column(String(64))
    chunk_strategy = Column(String(32))
    chunk_config = Column(JSON)
    created_by = Column(String(20), nullable=False)
    updated_by = Column(String(20))


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "t_knowledge_chunk"

    id = Column(String(20), primary_key=True, default=gen_id)
    kb_id = Column(String(20), nullable=False)
    doc_id = Column(String(20), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64))
    char_count = Column(Integer)
    token_count = Column(Integer)
    enabled = Column(SmallInteger, default=1)
    created_by = Column(String(20), nullable=False)
    updated_by = Column(String(20))


class IntentNode(Base, TimestampMixin):
    __tablename__ = "t_intent_node"

    id = Column(String(20), primary_key=True, default=gen_id)
    kb_id = Column(String(20))
    intent_code = Column(String(64), nullable=False)
    name = Column(String(64), nullable=False)
    level = Column(SmallInteger, nullable=False, default=1)
    parent_intent_code = Column(String(64))
    description = Column(String(255))
    collection_name = Column(String(64))
    search_channels = Column(JSON)
    prompt_template = Column(Text)
    sort_order = Column(Integer, default=0)
    enabled = Column(SmallInteger, default=1)


class QueryTermMapping(Base, TimestampMixin):
    __tablename__ = "t_query_term_mapping"

    id = Column(String(20), primary_key=True, default=gen_id)
    kb_id = Column(String(20))
    source_term = Column(String(128), nullable=False)
    target_term = Column(String(128), nullable=False)
    mapping_type = Column(String(32), default="EXACT")
    enabled = Column(SmallInteger, default=1)


class SampleQuestion(Base, TimestampMixin):
    __tablename__ = "t_sample_question"

    id = Column(String(20), primary_key=True, default=gen_id)
    kb_id = Column(String(20))
    question = Column(String(512), nullable=False)
    sort_order = Column(Integer, default=0)
    enabled = Column(SmallInteger, default=1)


class RagTraceRun(Base):
    __tablename__ = "t_rag_trace_run"
    __table_args__ = {"extend_existing": True}

    id = Column(String(20), primary_key=True, default=gen_id)
    conversation_id = Column(String(20), nullable=False)
    message_id = Column(String(20))
    user_id = Column(String(20), nullable=False)
    query = Column(Text, nullable=False)
    rewrite_query = Column(Text)
    intent = Column(String(64))
    search_duration_ms = Column(Integer)
    llm_duration_ms = Column(Integer)
    total_duration_ms = Column(Integer)
    recall_count = Column(Integer)
    final_count = Column(Integer)
    model_name = Column(String(64))
    status = Column(String(16), default="success")
    error_message = Column(Text)
    create_time = Column(DateTime, default=_utcnow)


class RagTraceNode(Base):
    __tablename__ = "t_rag_trace_node"
    __table_args__ = {"extend_existing": True}

    id = Column(String(20), primary_key=True, default=gen_id)
    trace_run_id = Column(String(20), nullable=False)
    node_type = Column(String(32), nullable=False)
    node_name = Column(String(64))
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    duration_ms = Column(Integer)
    input_data = Column(JSON)
    output_data = Column(JSON)
    status = Column(String(16), default="success")
    error_message = Column(Text)
    create_time = Column(DateTime, default=_utcnow)
