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


class Tenant(Base):
    __tablename__ = "t_tenant"

    id = Column(String(20), primary_key=True, default=gen_id)
    name = Column(String(128), nullable=False)
    enabled = Column(SmallInteger, default=1)
    create_time = Column(DateTime, default=_utcnow)
    update_time = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Department(Base, TimestampMixin):
    __tablename__ = "t_department"

    id = Column(String(20), primary_key=True, default=gen_id)
    tenant_id = Column(String(64), nullable=False)
    parent_id = Column(String(20))
    name = Column(String(128), nullable=False)
    created_by = Column(String(20), nullable=False)


class User(Base, TimestampMixin):
    __tablename__ = "t_user"

    id = Column(String(20), primary_key=True, default=gen_id)
    username = Column(String(64), nullable=False, unique=True)
    password = Column(String(128), nullable=False)
    role = Column(String(32), nullable=False, default="user")
    avatar = Column(String(128))
    tenant_id = Column(String(64), nullable=False, default="default")
    department_id = Column(String(64))


class Conversation(Base, TimestampMixin):
    __tablename__ = "t_conversation"

    id = Column(String(20), primary_key=True, default=gen_id)
    conversation_id = Column(String(20), nullable=False)
    user_id = Column(String(20), nullable=False)
    tenant_id = Column(String(64), nullable=False, default="default")
    title = Column(String(128), nullable=False, default="新对话")
    last_time = Column(DateTime)
    summary = Column(Text)
    summary_message_count = Column(Integer, default=0)


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
    agent_steps = Column(JSON)
    rag_modes = Column(JSON)
    retrieval_channels = Column(JSON)
    hyde_doc = Column(Text)
    hyde_meta = Column(JSON)


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
    pipeline_id = Column(String(20))
    tenant_id = Column(String(64), nullable=False, default="default")
    department_id = Column(String(64))
    visibility = Column(String(16), nullable=False, default="PRIVATE")
    created_by = Column(String(20), nullable=False)
    updated_by = Column(String(20))


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "t_knowledge_document"

    id = Column(String(20), primary_key=True, default=gen_id)
    kb_id = Column(String(20), nullable=False)
    tenant_id = Column(String(64), nullable=False, default="default")
    department_id = Column(String(64))
    visibility = Column(String(16), nullable=False, default="INHERIT")
    doc_name = Column(String(256), nullable=False)
    enabled = Column(SmallInteger, default=1)
    chunk_count = Column(Integer, default=0)
    file_url = Column(String(1024), nullable=False)
    file_type = Column(String(16), nullable=False)
    file_size = Column(BigInteger)
    content_hash = Column(String(64))  # SHA-256 hex digest for dedup + incremental indexing
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
    tenant_id = Column(String(64), nullable=False, default="default")
    department_id = Column(String(64))
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding_content = Column(Text)
    content_hash = Column(String(64))
    char_count = Column(Integer)
    token_count = Column(Integer)
    block_type = Column(String(32))
    page_start = Column(Integer)
    page_end = Column(Integer)
    bbox_json = Column(JSON)
    metadata_json = Column(JSON)
    enabled = Column(SmallInteger, default=1)
    created_by = Column(String(20), nullable=False)
    updated_by = Column(String(20))


class KnowledgeAsset(Base, TimestampMixin):
    __tablename__ = "t_knowledge_asset"

    id = Column(String(32), primary_key=True)
    kb_id = Column(String(20), nullable=False)
    doc_id = Column(String(20), nullable=False)
    tenant_id = Column(String(64), nullable=False, default="default")
    department_id = Column(String(64))
    asset_type = Column(String(32), nullable=False, default="IMAGE")
    mime_type = Column(String(128), nullable=False)
    file_name = Column(String(512))
    file_size = Column(BigInteger)
    content_hash = Column(String(64), nullable=False)
    storage_key = Column(String(1024), nullable=False)
    storage_url = Column(String(2048), nullable=False)
    page_no = Column(Integer)
    bbox_json = Column(JSON)
    description = Column(Text)
    metadata_json = Column(JSON)
    created_by = Column(String(20), nullable=False)
    updated_by = Column(String(20))


class IntentNode(Base, TimestampMixin):
    __tablename__ = "t_intent_node"

    id = Column(String(20), primary_key=True, default=gen_id)
    tenant_id = Column(String(64), nullable=False, default="default")
    kb_id = Column(String(20))
    intent_code = Column(String(64), nullable=False)
    name = Column(String(64), nullable=False)
    level = Column(SmallInteger, nullable=False, default=1)
    parent_intent_code = Column(String(64))
    description = Column(String(255))
    collection_name = Column(String(64))
    search_channels = Column(JSON)
    prompt_template = Column(Text)
    kind = Column(String(16), nullable=False, default="KB")
    score_threshold = Column(Integer, default=30)
    examples = Column(JSON)
    mcp_tool_id = Column(String(128))
    sort_order = Column(Integer, default=0)
    enabled = Column(SmallInteger, default=1)


class QueryTermMapping(Base, TimestampMixin):
    __tablename__ = "t_query_term_mapping"

    id = Column(String(20), primary_key=True, default=gen_id)
    tenant_id = Column(String(64), nullable=False, default="default")
    kb_id = Column(String(20))
    source_term = Column(String(128), nullable=False)
    target_term = Column(String(128), nullable=False)
    mapping_type = Column(String(32), default="EXACT")
    enabled = Column(SmallInteger, default=1)
    hit_count = Column(Integer, default=0)


class SampleQuestion(Base, TimestampMixin):
    __tablename__ = "t_sample_question"

    id = Column(String(20), primary_key=True, default=gen_id)
    tenant_id = Column(String(64), nullable=False, default="default")
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
    tenant_id = Column(String(64), nullable=False, default="default")
    kb_id = Column(String(20))
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
    rejection_reason = Column(String(64))
    metadata_json = Column(JSON)
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


class ResourceACL(Base, TimestampMixin):
    __tablename__ = "t_resource_acl"

    id = Column(String(20), primary_key=True, default=gen_id)
    tenant_id = Column(String(64), nullable=False)
    subject_type = Column(String(24), nullable=False)
    subject_id = Column(String(64), nullable=False)
    resource_type = Column(String(24), nullable=False)
    resource_id = Column(String(20), nullable=False)
    permission = Column(String(16), nullable=False, default="READ")
    created_by = Column(String(20), nullable=False)


class IndexSyncJob(Base, TimestampMixin):
    __tablename__ = "t_index_sync_job"

    id = Column(String(20), primary_key=True, default=gen_id)
    tenant_id = Column(String(64), nullable=False)
    kb_id = Column(String(20), nullable=False)
    doc_id = Column(String(20), nullable=False)
    operation = Column(String(24), nullable=False)
    payload_json = Column(JSON)
    channel_status_json = Column(JSON)
    status = Column(String(16), nullable=False, default="PENDING")
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text)
    next_retry_time = Column(DateTime)


# ============================================================
# Batch Import
# ============================================================


class BatchImportJob(Base, TimestampMixin):
    __tablename__ = "t_batch_import_job"

    id = Column(String(20), primary_key=True, default=gen_id)
    tenant_id = Column(String(64), nullable=False, default="default")
    kb_id = Column(String(20), nullable=False)
    total_files = Column(Integer, nullable=False, default=0)
    completed_files = Column(Integer, nullable=False, default=0)
    failed_files = Column(Integer, nullable=False, default=0)
    skipped_duplicates = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default="pending")  # pending/running/success/partial/error
    file_results = Column(JSON)  # list of per-file results
    error_message = Column(Text)
    created_by = Column(String(20), nullable=False)


class BatchImportFileRecord(Base):
    __tablename__ = "t_batch_import_file"
    __table_args__ = {"extend_existing": True}

    id = Column(String(20), primary_key=True, default=gen_id)
    job_id = Column(String(20), nullable=False)
    file_name = Column(String(512), nullable=False)
    file_size = Column(BigInteger)
    file_type = Column(String(16))
    status = Column(String(16), nullable=False, default="pending")  # pending/running/success/duplicate/error
    doc_id = Column(String(20))
    chunk_count = Column(Integer, default=0)
    error_message = Column(Text)
    create_time = Column(DateTime, default=_utcnow)


# ============================================================
# Audit
# ============================================================


class BizChangeLog(Base):
    __tablename__ = "t_biz_change_log"
    __table_args__ = {"extend_existing": True}

    id = Column(String(20), primary_key=True, default=gen_id)
    biz_type = Column(String(64), nullable=False)
    biz_id = Column(String(64), nullable=False)
    operation_type = Column(String(32), nullable=False)
    action_desc = Column(String(512))
    before_snapshot = Column(JSON)
    after_snapshot = Column(JSON)
    change_diff = Column(JSON)
    operator_id = Column(String(64))
    operator_name = Column(String(128))
    operator_role = Column(String(64))
    success = Column(SmallInteger, default=1)
    error_message = Column(Text)
    class_name = Column(String(255))
    method_name = Column(String(255))
    ip = Column(String(64))
    user_agent = Column(String(512))
    create_time = Column(DateTime, default=_utcnow)


# ============================================================
# Document Schedule (timed refresh)
# ============================================================


class KnowledgeDocumentSchedule(Base):
    __tablename__ = "t_knowledge_document_schedule"
    __table_args__ = {"extend_existing": True}

    id = Column(String(20), primary_key=True, default=gen_id)
    doc_id = Column(String(20), nullable=False, unique=True)
    kb_id = Column(String(20), nullable=False)
    cron_expr = Column(String(64))
    enabled = Column(SmallInteger, default=0)
    next_run_time = Column(DateTime)
    last_run_time = Column(DateTime)
    last_success_time = Column(DateTime)
    last_status = Column(String(16))
    last_error = Column(String(512))
    last_etag = Column(String(256))
    last_modified = Column(String(256))
    last_content_hash = Column(String(128))
    lock_owner = Column(String(128))
    lock_until = Column(DateTime)
    create_time = Column(DateTime, default=_utcnow)
    update_time = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class KnowledgeDocumentScheduleExec(Base):
    __tablename__ = "t_knowledge_document_schedule_exec"
    __table_args__ = {"extend_existing": True}

    id = Column(String(20), primary_key=True, default=gen_id)
    schedule_id = Column(String(20), nullable=False)
    doc_id = Column(String(20), nullable=False)
    kb_id = Column(String(20), nullable=False)
    status = Column(String(16), nullable=False)
    message = Column(String(512))
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    file_name = Column(String(512))
    file_size = Column(BigInteger)
    content_hash = Column(String(128))
    etag = Column(String(256))
    last_modified = Column(String(256))
    create_time = Column(DateTime, default=_utcnow)
    update_time = Column(DateTime, default=_utcnow, onupdate=_utcnow)


# ============================================================
# Document Chunk Processing Log
# ============================================================


class KnowledgeDocumentChunkLog(Base):
    __tablename__ = "t_knowledge_document_chunk_log"
    __table_args__ = {"extend_existing": True}

    id = Column(String(20), primary_key=True, default=gen_id)
    doc_id = Column(String(20), nullable=False)
    status = Column(String(16), nullable=False)
    process_mode = Column(String(16))
    chunk_strategy = Column(String(16))
    pipeline_id = Column(String(20))
    extract_duration = Column(BigInteger)
    chunk_duration = Column(BigInteger)
    embed_duration = Column(BigInteger)
    persist_duration = Column(BigInteger)
    total_duration = Column(BigInteger)
    chunk_count = Column(Integer)
    error_message = Column(Text)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    create_time = Column(DateTime, default=_utcnow)
    update_time = Column(DateTime, default=_utcnow, onupdate=_utcnow)


# ============================================================
# Ingestion Pipeline
# ============================================================


class IngestionPipeline(Base):
    __tablename__ = "t_ingestion_pipeline"
    __table_args__ = {"extend_existing": True}

    id = Column(String(20), primary_key=True, default=gen_id)
    tenant_id = Column(String(64), nullable=False, default="default")
    name = Column(String(128), nullable=False)
    description = Column(String(512))
    enabled = Column(SmallInteger, nullable=False, default=1)
    created_by = Column(String(20), nullable=False)
    updated_by = Column(String(20))
    create_time = Column(DateTime, default=_utcnow)
    update_time = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    deleted = Column(SmallInteger, default=0)


class IngestionPipelineNode(Base):
    __tablename__ = "t_ingestion_pipeline_node"
    __table_args__ = {"extend_existing": True}

    id = Column(String(20), primary_key=True, default=gen_id)
    pipeline_id = Column(String(20), nullable=False)
    node_id = Column(String(64), nullable=False)
    node_type = Column(String(32), nullable=False)
    next_node_id = Column(String(64))
    settings_json = Column(JSON)
    condition_json = Column(JSON)
    created_by = Column(String(20), nullable=False)
    updated_by = Column(String(20))
    create_time = Column(DateTime, default=_utcnow)
    update_time = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    deleted = Column(SmallInteger, default=0)


class IngestionTask(Base):
    __tablename__ = "t_ingestion_task"
    __table_args__ = {"extend_existing": True}

    id = Column(String(20), primary_key=True, default=gen_id)
    tenant_id = Column(String(64), nullable=False, default="default")
    pipeline_id = Column(String(20), nullable=False)
    kb_id = Column(String(20))
    doc_id = Column(String(20))
    trace_id = Column(String(32))
    idempotency_key = Column(String(128))
    parent_task_id = Column(String(20))
    attempt = Column(Integer, nullable=False, default=1)
    source_type = Column(String(32), nullable=False)
    source_location = Column(String(1024), nullable=False)
    source_file_name = Column(String(512))
    status = Column(String(16), default="pending")
    total_duration_ms = Column(BigInteger)
    sla_ms = Column(BigInteger, nullable=False, default=300000)
    heartbeat_at = Column(DateTime)
    chunk_count = Column(Integer, default=0)
    error_message = Column(Text)
    logs_json = Column(JSON)
    metadata_json = Column(JSON)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_by = Column(String(20), nullable=False)
    updated_by = Column(String(20))
    create_time = Column(DateTime, default=_utcnow)
    update_time = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    deleted = Column(SmallInteger, default=0)


class IngestionTaskNode(Base):
    __tablename__ = "t_ingestion_task_node"
    __table_args__ = {"extend_existing": True}

    id = Column(String(20), primary_key=True, default=gen_id)
    task_id = Column(String(20), nullable=False)
    pipeline_id = Column(String(20), nullable=False)
    node_id = Column(String(64), nullable=False)
    node_type = Column(String(32), nullable=False)
    node_order = Column(Integer, default=0)
    attempt = Column(Integer, nullable=False, default=1)
    status = Column(String(16), default="pending")
    duration_ms = Column(BigInteger)
    message = Column(String(512))
    error_message = Column(Text)
    output_json = Column(JSON)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    create_time = Column(DateTime, default=_utcnow)
    update_time = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    deleted = Column(SmallInteger, default=0)


class EvaluationRun(Base):
    __tablename__ = "t_evaluation_run"

    id = Column(String(20), primary_key=True, default=gen_id)
    tenant_id = Column(String(64), nullable=False, default="default")
    kb_id = Column(String(20), nullable=False)
    kb_name = Column(String(128), nullable=False)
    dataset_version = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="running")
    gate_status = Column(String(16), nullable=False, default="pending")
    config_json = Column(JSON, nullable=False)
    metrics_json = Column(JSON)
    slices_json = Column(JSON)
    gates_json = Column(JSON)
    baseline_run_id = Column(String(20))
    deltas_json = Column(JSON)
    results_json = Column(JSON)
    duration_ms = Column(BigInteger)
    started_at = Column(DateTime, default=_utcnow)
    completed_at = Column(DateTime)
    created_by = Column(String(20), nullable=False)
    create_time = Column(DateTime, default=_utcnow)


class IngestionJob(Base, TimestampMixin):
    """Outbox record for asynchronous document ingestion.

    Uploads enqueue a job inside the same transaction that creates the
    document; workers claim due jobs (FOR UPDATE SKIP LOCKED on PostgreSQL)
    and execute ingestion out of the request path.
    """

    __tablename__ = "t_ingestion_job"

    id = Column(String(20), primary_key=True, default=gen_id)
    tenant_id = Column(String(64), nullable=False, default="default")
    kb_id = Column(String(20), nullable=False)
    doc_id = Column(String(20), nullable=False)
    pipeline_id = Column(String(20))
    source_type = Column(String(32), nullable=False, default="file")
    file_path = Column(String(1024), nullable=False)
    chunk_strategy = Column(String(32), nullable=False, default="FIXED_WINDOW")
    chunk_config_json = Column(JSON)
    operation = Column(String(24), nullable=False, default="INGEST")
    status = Column(String(16), nullable=False, default="QUEUED")
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    next_retry_time = Column(DateTime)
    claimed_by = Column(String(64))
    claimed_at = Column(DateTime)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_ms = Column(BigInteger)
    chunk_count = Column(Integer, default=0)
    error_message = Column(Text)
    created_by = Column(String(20), nullable=False)
