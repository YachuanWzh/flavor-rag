-- ============================================================
-- flavor-rag PostgreSQL Schema
-- 基于 rag开发指导.md 第 3 节数据库设计
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 用户与会话
-- ============================================================

-- 用户表
CREATE TABLE t_user (
    id           VARCHAR(20)  PRIMARY KEY,
    username     VARCHAR(64)  NOT NULL UNIQUE,
    password     VARCHAR(128) NOT NULL,
    role         VARCHAR(32)  NOT NULL DEFAULT 'user',  -- admin / user
    avatar       VARCHAR(128),
    create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted      SMALLINT    DEFAULT 0
);

-- 会话表
CREATE TABLE t_conversation (
    id              VARCHAR(20) PRIMARY KEY,
    conversation_id VARCHAR(20) NOT NULL,
    user_id         VARCHAR(20) NOT NULL,
    title           VARCHAR(128) NOT NULL DEFAULT '新对话',
    last_time       TIMESTAMP,
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
    chunk_strategy   VARCHAR(32),                     -- FIXED_SIZE / STRUCTURE_AWARE / BLOCK_AWARE
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
    chunk_index  INTEGER     NOT NULL,
    content      TEXT        NOT NULL,
    content_hash VARCHAR(64),
    char_count   INTEGER,
    token_count  INTEGER,
    enabled      SMALLINT    DEFAULT 1,
    created_by   VARCHAR(20) NOT NULL,
    updated_by   VARCHAR(20),
    create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted      SMALLINT    DEFAULT 0
);
CREATE INDEX idx_doc_id ON t_knowledge_chunk (doc_id);

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
