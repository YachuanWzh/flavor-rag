-- ============================================================
-- flavor-rag PostgreSQL Schema
-- 基于 rag开发指导.md 第 3 节数据库设计
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 用户与会话
-- ============================================================

-- 用户表
CREATE TABLE t_tenant (
    id           VARCHAR(20) PRIMARY KEY,
    name         VARCHAR(128) NOT NULL,
    enabled      SMALLINT DEFAULT 1,
    create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE t_department (
    id           VARCHAR(20) PRIMARY KEY,
    tenant_id    VARCHAR(64) NOT NULL,
    parent_id    VARCHAR(20),
    name         VARCHAR(128) NOT NULL,
    created_by   VARCHAR(20) NOT NULL,
    create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted      SMALLINT DEFAULT 0
);
CREATE INDEX idx_department_tenant ON t_department (tenant_id);

CREATE TABLE t_user (
    id           VARCHAR(20)  PRIMARY KEY,
    username     VARCHAR(64)  NOT NULL UNIQUE,
    password     VARCHAR(128) NOT NULL,
    role         VARCHAR(32)  NOT NULL DEFAULT 'user',  -- admin / user
    avatar       VARCHAR(128),
    tenant_id    VARCHAR(64)  NOT NULL DEFAULT 'default',
    department_id VARCHAR(64),
    create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted      SMALLINT    DEFAULT 0
);

-- 会话表
CREATE TABLE t_conversation (
    id              VARCHAR(20) PRIMARY KEY,
    conversation_id VARCHAR(20) NOT NULL,
    user_id         VARCHAR(20) NOT NULL,
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'default',
    title           VARCHAR(128) NOT NULL DEFAULT '新对话',
    last_time       TIMESTAMP,
    summary         TEXT,
    summary_message_count INTEGER DEFAULT 0,
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted         SMALLINT    DEFAULT 0,
    CONSTRAINT uk_conversation_user UNIQUE (conversation_id, user_id)
);
CREATE INDEX idx_user_time ON t_conversation (user_id, last_time);

-- 消息表
CREATE TABLE t_message (
    id                VARCHAR(20) PRIMARY KEY,
    conversation_id   VARCHAR(20) NOT NULL,
    user_id           VARCHAR(20) NOT NULL,
    role              VARCHAR(16) NOT NULL,         -- user / assistant
    content           TEXT        NOT NULL,
    thinking_content  TEXT,                          -- 深度思考内容
    thinking_duration INTEGER,
    sources           JSONB,                         -- 引用来源
    recommended_questions JSONB,                    -- 推荐问题
    message_status    VARCHAR(16) DEFAULT 'NORMAL', -- NORMAL / INTERRUPTED
    agent_steps       JSONB,
    rag_modes         JSONB,
    retrieval_channels JSONB,
    create_time       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted           SMALLINT    DEFAULT 0
);
CREATE INDEX idx_conversation_time ON t_message (conversation_id, user_id, create_time);

-- 消息反馈表
CREATE TABLE t_message_feedback (
    id              VARCHAR(20) PRIMARY KEY,
    message_id      VARCHAR(20) NOT NULL,
    conversation_id VARCHAR(20) NOT NULL,
    user_id         VARCHAR(20) NOT NULL,
    vote            SMALLINT    NOT NULL,  -- 1=赞, -1=踩
    reason          VARCHAR(255),
    comment         VARCHAR(1024),
    create_time     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted         SMALLINT    NOT NULL DEFAULT 0,
    CONSTRAINT uk_msg_user UNIQUE (message_id, user_id)
);

-- 线上问题评测资产（回答完成后自动生成 base case，可人工提升为 golden）
CREATE TABLE t_evaluation_dataset_case (
    id                    VARCHAR(20) PRIMARY KEY,
    tenant_id             VARCHAR(64) NOT NULL DEFAULT 'default',
    source_question_id    VARCHAR(20) NOT NULL,
    source_answer_id      VARCHAR(20),
    user_id               VARCHAR(20) NOT NULL,
    conversation_id       VARCHAR(20) NOT NULL,
    case_type             VARCHAR(16) NOT NULL DEFAULT 'base',
    review_status         VARCHAR(24) NOT NULL DEFAULT 'generated',
    question              TEXT NOT NULL,
    expected_answer       TEXT NOT NULL DEFAULT '',
    expected_chunk_ids    JSONB NOT NULL DEFAULT '[]',
    expected_doc_ids      JSONB NOT NULL DEFAULT '[]',
    retrieved_chunk_ids   JSONB NOT NULL DEFAULT '[]',
    retrieved_doc_ids     JSONB NOT NULL DEFAULT '[]',
    knowledge_base_ids    JSONB NOT NULL DEFAULT '[]',
    category              VARCHAR(64) NOT NULL DEFAULT 'production',
    difficulty            VARCHAR(16) NOT NULL DEFAULT 'medium',
    tags                  JSONB NOT NULL DEFAULT '[]',
    answerable            SMALLINT NOT NULL DEFAULT 1,
    active                SMALLINT NOT NULL DEFAULT 0,
    quality_score         DOUBLE PRECISION NOT NULL DEFAULT 0,
    feedback_vote         SMALLINT,
    feedback_reason       VARCHAR(255),
    feedback_comment      VARCHAR(1024),
    promoted_by           VARCHAR(20),
    promoted_at           TIMESTAMP,
    create_time           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted               SMALLINT DEFAULT 0,
    CONSTRAINT uq_evaluation_case_source_question
        UNIQUE (tenant_id, source_question_id)
);
CREATE INDEX idx_evaluation_case_tenant_status
    ON t_evaluation_dataset_case (tenant_id, case_type, active, create_time);
CREATE INDEX idx_evaluation_case_user
    ON t_evaluation_dataset_case (tenant_id, user_id, create_time);

-- ============================================================
-- 知识库与文档
-- ============================================================

-- 知识库表
CREATE TABLE t_knowledge_base (
    id              VARCHAR(20) PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    embedding_model VARCHAR(64)  NOT NULL,          -- 嵌入模型标识
    collection_name VARCHAR(64)  NOT NULL UNIQUE,    -- Milvus Collection 名
    active_collection_name VARCHAR(128),
    active_index_generation VARCHAR(32) NOT NULL DEFAULT 'v1',
    pipeline_id     VARCHAR(20),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'default',
    department_id   VARCHAR(64),
    visibility      VARCHAR(16) NOT NULL DEFAULT 'PRIVATE',
    created_by      VARCHAR(20)  NOT NULL,
    updated_by      VARCHAR(20),
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted         SMALLINT    DEFAULT 0
);

-- 文档表
CREATE TABLE t_knowledge_document (
    id               VARCHAR(20) PRIMARY KEY,
    kb_id            VARCHAR(20)  NOT NULL,
    tenant_id        VARCHAR(64) NOT NULL DEFAULT 'default',
    department_id    VARCHAR(64),
    visibility       VARCHAR(16) NOT NULL DEFAULT 'INHERIT',
    doc_name         VARCHAR(256) NOT NULL,
    enabled          SMALLINT     DEFAULT 1,
    chunk_count      INTEGER      DEFAULT 0,
    file_url         VARCHAR(1024) NOT NULL,         -- S3 存储路径
    file_type        VARCHAR(16)  NOT NULL,           -- pdf / docx / xlsx / md / txt
    file_size        BIGINT,
    content_hash     VARCHAR(64),                     -- SHA-256 digest for dedup + incremental indexing
    process_mode     VARCHAR(16)  DEFAULT 'chunk',    -- chunk / pipeline
    status           VARCHAR(16)  DEFAULT 'pending',  -- pending/running/success/failed
    source_type      VARCHAR(16),                     -- file / url
    source_location  VARCHAR(1024),                   -- 源 URL（URL 类型文档）
    schedule_enabled SMALLINT,                        -- 是否启用定时刷新
    schedule_cron    VARCHAR(64),                     -- Cron 表达式
    chunk_strategy   VARCHAR(32),                     -- FIXED_WINDOW (固定窗口) / SEMANTIC (语义切分)
    chunk_config     JSONB,                           -- 分块配置 JSON
    active_generation VARCHAR(32) NOT NULL DEFAULT 'v1',
    pending_generation VARCHAR(32),
    created_by       VARCHAR(20)  NOT NULL,
    updated_by       VARCHAR(20),
    create_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted          SMALLINT    DEFAULT 0
);
CREATE INDEX idx_kb_id ON t_knowledge_document (kb_id);

-- 分块表（关系型元数据；向量数据存 Milvus）
CREATE TABLE t_knowledge_chunk (
    id           VARCHAR(20) PRIMARY KEY,
    kb_id        VARCHAR(20) NOT NULL,
    doc_id       VARCHAR(20) NOT NULL,
    tenant_id    VARCHAR(64) NOT NULL DEFAULT 'default',
    department_id VARCHAR(64),
    chunk_index  INTEGER     NOT NULL,
    content      TEXT        NOT NULL,
    embedding_content TEXT,
    content_hash VARCHAR(64),
    char_count   INTEGER,
    token_count  INTEGER,
    block_type   VARCHAR(32),
    page_start   INTEGER,
    page_end     INTEGER,
    bbox_json    JSONB,
    metadata_json JSONB,
    generation   VARCHAR(32) NOT NULL DEFAULT 'v1',
    index_status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    enabled      SMALLINT    DEFAULT 1,
    created_by   VARCHAR(20) NOT NULL,
    updated_by   VARCHAR(20),
    create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted      SMALLINT    DEFAULT 0
);
CREATE INDEX idx_doc_id ON t_knowledge_chunk (doc_id);

-- PDF 图片等多模态资产
CREATE TABLE t_knowledge_asset (
    id            VARCHAR(32) PRIMARY KEY,
    kb_id         VARCHAR(20)  NOT NULL,
    doc_id        VARCHAR(20)  NOT NULL,
    tenant_id     VARCHAR(64) NOT NULL DEFAULT 'default',
    department_id VARCHAR(64),
    asset_type    VARCHAR(32)  NOT NULL DEFAULT 'IMAGE',
    mime_type     VARCHAR(128) NOT NULL,
    file_name     VARCHAR(512),
    file_size     BIGINT,
    content_hash  VARCHAR(64)  NOT NULL,
    storage_key   VARCHAR(1024) NOT NULL,
    storage_url   VARCHAR(2048) NOT NULL,
    page_no       INTEGER,
    bbox_json     JSONB,
    description   TEXT,
    metadata_json JSONB,
    generation    VARCHAR(32) NOT NULL DEFAULT 'v1',
    index_status  VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    created_by    VARCHAR(20) NOT NULL,
    updated_by    VARCHAR(20),
    create_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted       SMALLINT DEFAULT 0
);
CREATE INDEX idx_knowledge_asset_doc_id ON t_knowledge_asset (doc_id);
CREATE INDEX idx_knowledge_asset_hash ON t_knowledge_asset (content_hash);

CREATE TABLE t_resource_acl (
    id             VARCHAR(20) PRIMARY KEY,
    tenant_id      VARCHAR(64) NOT NULL,
    subject_type   VARCHAR(24) NOT NULL,
    subject_id     VARCHAR(64) NOT NULL,
    resource_type  VARCHAR(24) NOT NULL,
    resource_id    VARCHAR(20) NOT NULL,
    permission     VARCHAR(16) NOT NULL DEFAULT 'READ',
    created_by     VARCHAR(20) NOT NULL,
    create_time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted        SMALLINT DEFAULT 0
);
CREATE INDEX idx_acl_resource ON t_resource_acl (tenant_id, resource_type, resource_id);
CREATE INDEX idx_acl_subject ON t_resource_acl (tenant_id, subject_type, subject_id);

CREATE TABLE t_index_sync_job (
    id                  VARCHAR(20) PRIMARY KEY,
    tenant_id           VARCHAR(64) NOT NULL,
    kb_id               VARCHAR(20) NOT NULL,
    doc_id              VARCHAR(20) NOT NULL,
    operation           VARCHAR(24) NOT NULL,
    payload_json        JSONB,
    channel_status_json JSONB,
    status              VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    attempts            INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT,
    next_retry_time     TIMESTAMP,
    create_time         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted             SMALLINT DEFAULT 0
);
CREATE INDEX idx_sync_job_status ON t_index_sync_job (status, next_retry_time);
CREATE INDEX idx_kb_tenant ON t_knowledge_base (tenant_id, deleted);
CREATE INDEX idx_document_tenant ON t_knowledge_document (tenant_id, kb_id, deleted);
CREATE INDEX idx_chunk_tenant ON t_knowledge_chunk (tenant_id, kb_id, doc_id, deleted, enabled);
CREATE UNIQUE INDEX uq_acl_live_grant ON t_resource_acl
    (tenant_id, subject_type, subject_id, resource_type, resource_id)
    WHERE deleted = 0;

-- ============================================================
-- 意图树与查询词映射
-- ============================================================

-- 意图树节点表
CREATE TABLE t_intent_node (
    id                    VARCHAR(20) PRIMARY KEY,
    kb_id                 VARCHAR(20),
    intent_code           VARCHAR(64)  NOT NULL,
    name                  VARCHAR(64)  NOT NULL,
    level                 SMALLINT     NOT NULL DEFAULT 1,
    parent_intent_code    VARCHAR(64),
    description           VARCHAR(255),
    collection_name       VARCHAR(64),
    search_channels       JSONB,
    prompt_template       TEXT,
    sort_order            INTEGER      DEFAULT 0,
    enabled               SMALLINT     DEFAULT 1,
    create_time           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted               SMALLINT    DEFAULT 0
);

-- 查询词映射表
CREATE TABLE t_query_term_mapping (
    id           VARCHAR(20) PRIMARY KEY,
    kb_id        VARCHAR(20),
    source_term  VARCHAR(128) NOT NULL,
    target_term  VARCHAR(128) NOT NULL,
    mapping_type VARCHAR(32) DEFAULT 'EXACT',  -- EXACT / SYNONYM / ABBREVIATION
    enabled      SMALLINT DEFAULT 1,
    hit_count    INTEGER DEFAULT 0,
    create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted      SMALLINT DEFAULT 0
);

-- 示例问题表
CREATE TABLE t_sample_question (
    id           VARCHAR(20) PRIMARY KEY,
    kb_id        VARCHAR(20),
    question     VARCHAR(512) NOT NULL,
    sort_order   INTEGER DEFAULT 0,
    enabled      SMALLINT DEFAULT 1,
    create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted      SMALLINT DEFAULT 0
);

-- ============================================================
-- 链路追踪
-- ============================================================

CREATE TABLE t_rag_trace_run (
    id                VARCHAR(20) PRIMARY KEY,
    conversation_id   VARCHAR(20) NOT NULL,
    message_id        VARCHAR(20),
    user_id           VARCHAR(20) NOT NULL,
    tenant_id         VARCHAR(64) NOT NULL DEFAULT 'default',
    kb_id             VARCHAR(20),
    query             TEXT NOT NULL,
    rewrite_query     TEXT,
    intent            VARCHAR(64),
    search_duration_ms INTEGER,
    llm_duration_ms   INTEGER,
    total_duration_ms INTEGER,
    recall_count      INTEGER,
    final_count       INTEGER,
    model_name        VARCHAR(64),
    status            VARCHAR(16) DEFAULT 'success',
    error_message     TEXT,
    rejection_reason  VARCHAR(64),
    metadata_json     JSONB,
    create_time       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE t_rag_trace_node (
    id                VARCHAR(20) PRIMARY KEY,
    trace_run_id      VARCHAR(20) NOT NULL,
    node_type         VARCHAR(32) NOT NULL,  -- rewrite / intent / search / fusion / rerank / generate
    node_name         VARCHAR(64),
    start_time        TIMESTAMP,
    end_time          TIMESTAMP,
    duration_ms       INTEGER,
    input_data        JSONB,
    output_data       JSONB,
    status            VARCHAR(16) DEFAULT 'success',
    error_message     TEXT,
    create_time       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_trace_run_id ON t_rag_trace_node (trace_run_id);


-- ============================================================
-- 业务审计日志 (P0)
-- ============================================================

CREATE TABLE t_biz_change_log (
    id               VARCHAR(20)  PRIMARY KEY,
    biz_type         VARCHAR(64)  NOT NULL,
    biz_id           VARCHAR(64)  NOT NULL,
    operation_type   VARCHAR(32)  NOT NULL,
    action_desc      VARCHAR(512),
    before_snapshot  JSONB,
    after_snapshot   JSONB,
    change_diff      JSONB,
    operator_id      VARCHAR(64),
    operator_name    VARCHAR(128),
    operator_role    VARCHAR(64),
    success          SMALLINT     NOT NULL DEFAULT 1,
    error_message    TEXT,
    class_name       VARCHAR(255),
    method_name      VARCHAR(255),
    ip               VARCHAR(64),
    user_agent       VARCHAR(512),
    create_time      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_biz_change_log_biz ON t_biz_change_log (biz_type, biz_id);
CREATE INDEX idx_biz_change_log_time ON t_biz_change_log (create_time);
CREATE INDEX idx_biz_change_log_operator ON t_biz_change_log (operator_id);


-- ============================================================
-- 文档定时刷新调度 (P0)
-- ============================================================

CREATE TABLE t_knowledge_document_schedule (
    id                VARCHAR(20)  PRIMARY KEY,
    doc_id            VARCHAR(20)  NOT NULL UNIQUE,
    kb_id             VARCHAR(20)  NOT NULL,
    cron_expr         VARCHAR(64),
    enabled           SMALLINT     DEFAULT 0,
    next_run_time     TIMESTAMP,
    last_run_time     TIMESTAMP,
    last_success_time TIMESTAMP,
    last_status       VARCHAR(16),
    last_error        VARCHAR(512),
    last_etag         VARCHAR(256),
    last_modified     VARCHAR(256),
    last_content_hash VARCHAR(128),
    lock_owner        VARCHAR(128),
    lock_until        TIMESTAMP,
    create_time       TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time       TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_schedule_next_run ON t_knowledge_document_schedule (next_run_time);
CREATE INDEX idx_schedule_lock ON t_knowledge_document_schedule (lock_until);

CREATE TABLE t_knowledge_document_schedule_exec (
    id            VARCHAR(20)  PRIMARY KEY,
    schedule_id   VARCHAR(20)  NOT NULL,
    doc_id        VARCHAR(20)  NOT NULL,
    kb_id         VARCHAR(20)  NOT NULL,
    status        VARCHAR(16)  NOT NULL,
    message       VARCHAR(512),
    start_time    TIMESTAMP,
    end_time      TIMESTAMP,
    file_name     VARCHAR(512),
    file_size     BIGINT,
    content_hash  VARCHAR(128),
    etag          VARCHAR(256),
    last_modified VARCHAR(256),
    create_time   TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time   TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_sched_exec_time ON t_knowledge_document_schedule_exec (schedule_id, start_time);
CREATE INDEX idx_sched_exec_doc ON t_knowledge_document_schedule_exec (doc_id);


-- ============================================================
-- 文档入库耗时日志 (P0)
-- ============================================================

CREATE TABLE t_knowledge_document_chunk_log (
    id                 VARCHAR(20)  PRIMARY KEY,
    doc_id             VARCHAR(20)  NOT NULL,
    status             VARCHAR(16)  NOT NULL,
    process_mode       VARCHAR(16),
    chunk_strategy     VARCHAR(16),
    pipeline_id        VARCHAR(20),
    extract_duration   BIGINT,
    chunk_duration     BIGINT,
    embed_duration     BIGINT,
    persist_duration   BIGINT,
    total_duration     BIGINT,
    chunk_count        INTEGER,
    error_message      TEXT,
    start_time         TIMESTAMP,
    end_time           TIMESTAMP,
    create_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_doc_chunk_log ON t_knowledge_document_chunk_log (doc_id);


-- ============================================================
-- 入库流水线系统 (P0)
-- ============================================================

CREATE TABLE t_ingestion_pipeline (
    id          VARCHAR(20)  PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    description VARCHAR(512),
    created_by  VARCHAR(20)  NOT NULL,
    updated_by  VARCHAR(20),
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted     SMALLINT    DEFAULT 0
);

CREATE TABLE t_ingestion_pipeline_node (
    id             VARCHAR(20)  PRIMARY KEY,
    pipeline_id    VARCHAR(20)  NOT NULL,
    node_id        VARCHAR(64)  NOT NULL,
    node_type      VARCHAR(32)  NOT NULL,
    next_node_id   VARCHAR(64),
    settings_json  JSONB,
    condition_json JSONB,
    created_by     VARCHAR(20)  NOT NULL,
    updated_by     VARCHAR(20),
    create_time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted        SMALLINT    DEFAULT 0
);
CREATE INDEX idx_pipeline_node ON t_ingestion_pipeline_node (pipeline_id);

CREATE TABLE t_ingestion_task (
    id               VARCHAR(20)   PRIMARY KEY,
    pipeline_id      VARCHAR(20)   NOT NULL,
    source_type      VARCHAR(32)   NOT NULL,
    source_location  VARCHAR(1024) NOT NULL,
    source_file_name VARCHAR(512),
    status           VARCHAR(16)   DEFAULT 'pending',
    chunk_count      INTEGER       DEFAULT 0,
    error_message    TEXT,
    logs_json        JSONB,
    metadata_json    JSONB,
    started_at       TIMESTAMP,
    completed_at     TIMESTAMP,
    created_by       VARCHAR(20)   NOT NULL,
    updated_by       VARCHAR(20),
    create_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted          SMALLINT     DEFAULT 0
);
CREATE INDEX idx_task_pipeline ON t_ingestion_task (pipeline_id);
CREATE INDEX idx_task_status ON t_ingestion_task (status);

CREATE TABLE t_ingestion_task_node (
    id            VARCHAR(20)  PRIMARY KEY,
    task_id       VARCHAR(20)  NOT NULL,
    pipeline_id   VARCHAR(20)  NOT NULL,
    node_id       VARCHAR(64)  NOT NULL,
    node_type     VARCHAR(32)  NOT NULL,
    node_order    INTEGER      DEFAULT 0,
    status        VARCHAR(16)  DEFAULT 'pending',
    duration_ms   BIGINT,
    message       VARCHAR(512),
    error_message TEXT,
    output_json   JSONB,
    create_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted       SMALLINT    DEFAULT 0
);
CREATE INDEX idx_task_node_task ON t_ingestion_task_node (task_id);

-- ============================================================
-- 批量导入与去重
-- ============================================================

-- 内容哈希索引（加速去重查询）
CREATE INDEX IF NOT EXISTS idx_knowledge_document_content_hash
  ON t_knowledge_document (kb_id, content_hash)
  WHERE content_hash IS NOT NULL AND deleted = 0;

-- 批量导入任务
CREATE TABLE t_batch_import_job (
    id                 VARCHAR(20) PRIMARY KEY,
    tenant_id          VARCHAR(64) NOT NULL DEFAULT 'default',
    kb_id              VARCHAR(20) NOT NULL,
    total_files        INTEGER NOT NULL DEFAULT 0,
    completed_files    INTEGER NOT NULL DEFAULT 0,
    failed_files       INTEGER NOT NULL DEFAULT 0,
    skipped_duplicates INTEGER NOT NULL DEFAULT 0,
    status             VARCHAR(16) NOT NULL DEFAULT 'pending',
    file_results       JSONB,
    error_message      TEXT,
    config_json        JSONB,
    attempts           INTEGER NOT NULL DEFAULT 0,
    claimed_by         VARCHAR(64),
    claimed_at         TIMESTAMP,
    next_retry_time    TIMESTAMP,
    created_by         VARCHAR(20) NOT NULL,
    create_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted            SMALLINT DEFAULT 0
);
CREATE INDEX idx_batch_import_job_tenant ON t_batch_import_job (tenant_id, kb_id, status);

-- 批量导入文件明细
CREATE TABLE t_batch_import_file (
    id            VARCHAR(20) PRIMARY KEY,
    job_id        VARCHAR(20) NOT NULL,
    file_name     VARCHAR(512) NOT NULL,
    file_size     BIGINT,
    file_type     VARCHAR(16),
    status        VARCHAR(16) NOT NULL DEFAULT 'pending',
    doc_id        VARCHAR(20),
    chunk_count   INTEGER DEFAULT 0,
    error_message TEXT,
    source_location VARCHAR(1024),
    create_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_batch_import_file_job ON t_batch_import_file (job_id);

-- v0.0.5 durable ingestion and immutable vector-index generations.
CREATE TABLE IF NOT EXISTS t_ingestion_job (
    id VARCHAR(20) PRIMARY KEY,
    idempotency_key VARCHAR(64) NOT NULL UNIQUE,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    kb_id VARCHAR(20) NOT NULL,
    doc_id VARCHAR(20) NOT NULL,
    pipeline_id VARCHAR(20),
    source_type VARCHAR(32) NOT NULL DEFAULT 'file',
    file_path VARCHAR(1024) NOT NULL,
    chunk_strategy VARCHAR(32) NOT NULL DEFAULT 'FIXED_WINDOW',
    chunk_config_json JSONB,
    operation VARCHAR(24) NOT NULL DEFAULT 'INGEST',
    generation VARCHAR(32) NOT NULL DEFAULT 'v1',
    status VARCHAR(16) NOT NULL DEFAULT 'QUEUED',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_retry_time TIMESTAMP,
    claimed_by VARCHAR(64),
    claimed_at TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms BIGINT,
    chunk_count INTEGER DEFAULT 0,
    error_message TEXT,
    created_by VARCHAR(20) NOT NULL,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted SMALLINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS t_knowledge_index_generation (
    id VARCHAR(20) PRIMARY KEY,
    kb_id VARCHAR(20) NOT NULL,
    generation VARCHAR(32) NOT NULL,
    collection_name VARCHAR(128) NOT NULL,
    embedding_model VARCHAR(128) NOT NULL,
    embedding_dim INTEGER NOT NULL,
    parser_version VARCHAR(64),
    chunker_version VARCHAR(64),
    status VARCHAR(16) NOT NULL DEFAULT 'BUILDING',
    expected_chunks INTEGER DEFAULT 0,
    indexed_chunks INTEGER DEFAULT 0,
    activated_at TIMESTAMP,
    error_message TEXT,
    created_by VARCHAR(20) NOT NULL DEFAULT 'system',
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted SMALLINT DEFAULT 0,
    UNIQUE (kb_id, generation)
);

CREATE TABLE IF NOT EXISTS t_index_repair_job (
    id VARCHAR(20) PRIMARY KEY,
    kb_id VARCHAR(20) NOT NULL,
    doc_id VARCHAR(20) NOT NULL,
    generation VARCHAR(32) NOT NULL,
    channel VARCHAR(24) NOT NULL,
    operation VARCHAR(24) NOT NULL DEFAULT 'UPSERT',
    status VARCHAR(16) NOT NULL DEFAULT 'QUEUED',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_retry_time TIMESTAMP,
    last_error TEXT,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted SMALLINT DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_chunk_active_generation
  ON t_knowledge_chunk (doc_id, generation, index_status, deleted);
CREATE INDEX IF NOT EXISTS idx_asset_active_generation
  ON t_knowledge_asset (doc_id, generation, index_status, deleted);

CREATE TABLE IF NOT EXISTS t_evaluation_run (
    id VARCHAR(20) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    kb_id VARCHAR(20) NOT NULL,
    kb_name VARCHAR(128) NOT NULL,
    dataset_version VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    gate_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    config_json JSONB NOT NULL,
    metrics_json JSONB,
    slices_json JSONB,
    gates_json JSONB,
    baseline_run_id VARCHAR(20),
    deltas_json JSONB,
    results_json JSONB,
    duration_ms BIGINT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_by VARCHAR(64),
    claimed_at TIMESTAMP,
    next_retry_time TIMESTAMP,
    error_message TEXT,
    created_by VARCHAR(20) NOT NULL,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Private interview simulation materials and results.
CREATE TABLE IF NOT EXISTS t_interview_material (
    id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    kind VARCHAR(16) NOT NULL,
    file_name VARCHAR(256),
    mime_type VARCHAR(128),
    file_size BIGINT NOT NULL DEFAULT 0,
    content_hash VARCHAR(64) NOT NULL,
    extracted_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, kind)
);

CREATE TABLE IF NOT EXISTS t_interview_session (
    id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    conversation_id VARCHAR(20),
    kb_id VARCHAR(20),
    kb_name VARCHAR(128),
    target_role VARCHAR(128),
    user_focus TEXT,
    difficulty VARCHAR(16) NOT NULL DEFAULT 'senior',
    question_count INTEGER NOT NULL DEFAULT 12,
    resume_hash VARCHAR(64),
    jd_hash VARCHAR(64),
    status VARCHAR(16) NOT NULL DEFAULT 'IN_PROGRESS',
    overall_score DOUBLE PRECISION,
    dimension_scores JSONB,
    role_fit_breakdown JSONB,
    summary TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS t_interview_question (
    id VARCHAR(20) PRIMARY KEY,
    interview_id VARCHAR(20) NOT NULL,
    sequence INTEGER NOT NULL,
    category VARCHAR(16) NOT NULL,
    question TEXT NOT NULL,
    follow_up TEXT,
    rubric JSONB,
    source JSONB,
    metadata JSONB,
    agent_generated SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (interview_id, sequence)
);

CREATE TABLE IF NOT EXISTS t_interview_answer (
    id VARCHAR(20) PRIMARY KEY,
    interview_id VARCHAR(20) NOT NULL,
    question_id VARCHAR(20) NOT NULL,
    answer TEXT NOT NULL DEFAULT '',
    answer_language VARCHAR(16),
    skipped SMALLINT NOT NULL DEFAULT 0,
    score DOUBLE PRECISION,
    dimension_scores JSONB,
    analysis TEXT,
    strengths JSONB,
    improvements JSONB,
    reference_points JSONB,
    answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (interview_id, question_id)
);

CREATE TABLE IF NOT EXISTS t_interview_profile (
    id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) NOT NULL UNIQUE,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    dimension_scores JSONB,
    overall_score DOUBLE PRECISION,
    previous_overall_score DOUBLE PRECISION,
    delta DOUBLE PRECISION NOT NULL DEFAULT 0,
    trend VARCHAR(16) NOT NULL DEFAULT 'stable',
    interview_count INTEGER NOT NULL DEFAULT 0,
    latest_interview_id VARCHAR(20),
    target_role VARCHAR(128),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_interview_material_tenant_user
  ON t_interview_material (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_interview_session_user_status
  ON t_interview_session (user_id, status);
CREATE INDEX IF NOT EXISTS idx_interview_question_interview
  ON t_interview_question (interview_id, sequence);
CREATE INDEX IF NOT EXISTS idx_interview_profile_tenant_user
  ON t_interview_profile (tenant_id, user_id);
