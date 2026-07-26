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

-- ============================================================
-- 知识库与文档
-- ============================================================

-- 知识库表
CREATE TABLE t_knowledge_base (
    id              VARCHAR(20) PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    embedding_model VARCHAR(64)  NOT NULL,          -- 嵌入模型标识
    collection_name VARCHAR(64)  NOT NULL UNIQUE,    -- Milvus Collection 名
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
    process_mode     VARCHAR(16)  DEFAULT 'chunk',    -- chunk / pipeline
    status           VARCHAR(16)  DEFAULT 'pending',  -- pending/running/success/failed
    source_type      VARCHAR(16),                     -- file / url
    source_location  VARCHAR(1024),                   -- 源 URL（URL 类型文档）
    schedule_enabled SMALLINT,                        -- 是否启用定时刷新
    schedule_cron    VARCHAR(64),                     -- Cron 表达式
    chunk_strategy   VARCHAR(32),                     -- FIXED_WINDOW (固定窗口) / SEMANTIC (语义切分)
    chunk_config     JSONB,                           -- 分块配置 JSON
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
