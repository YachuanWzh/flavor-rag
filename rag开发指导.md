# RAG 智能问答系统 — 新项目开发指导

> 基于 Ragent 项目的架构设计与 RAG 核心原理，指导使用 **Python（服务端）+ React（前端）** 技术栈从零搭建一个企业级 RAG 系统，存储方案与 Ragent 保持一致。

---

## 目录

1. [技术栈总览](#1-技术栈总览)
2. [基础设施部署](#2-基础设施部署)
3. [数据库设计](#3-数据库设计)
4. [服务端项目结构](#4-服务端项目结构)
5. [核心 RAG 链路实现](#5-核心-rag-链路实现)
6. [API 接口设计](#6-api-接口设计)
7. [前端项目结构](#7-前端项目结构)
8. [前端核心实现](#8-前端核心实现)
9. [开发路线图](#9-开发路线图)

---

## 1. 技术栈总览

### 1.1 服务端（Python）

| 类别 | 选型 | 说明 |
|------|------|------|
| Web 框架 | **FastAPI** | 异步支持好、自动生成 OpenAPI 文档、SSE 原生支持 |
| ORM | **SQLAlchemy 2.0** + asyncpg | PostgreSQL 异步驱动 |
| 向量数据库客户端 | **pymilvus** | Milvus 官方 Python SDK |
| 搜索引擎客户端 | **elasticsearch-py** | ES 官方 Python 客户端 |
| 消息队列 | **rocketmq-client-python** | RocketMQ 客户端 |
| 对象存储 | **boto3** | S3 兼容协议，对接 RustFS/MinIO |
| 图数据库客户端 | **neo4j** | Neo4j 官方 Python 驱动 |
| 缓存 | **redis-py** (异步) | Redis 客户端 |
| LLM 调用 | **httpx** / **openai** | 统一 OpenAI 兼容接口调用各模型供应商 |
| 任务调度 | **APScheduler** | 定时刷新 URL 文档 |
| 文档解析 | **python-docx**, **openpyxl**, **PyPDF2**, **markitdown** | 多种文档格式解析 |
| 配置管理 | **pydantic-settings** | 类型安全的配置管理 |
| 数据校验 | **pydantic** | 请求/响应模型校验 |

### 1.2 前端（React）

| 类别 | 选型 |
|------|------|
| 框架 | React 18 + TypeScript |
| 构建工具 | Vite 5 |
| 路由 | React Router v6 |
| 状态管理 | Zustand |
| HTTP 请求 | Axios |
| 流式响应 | Fetch API + SSE (ReadableStream) |
| UI 组件 | shadcn/ui (Radix UI + Tailwind CSS) |
| 样式 | Tailwind CSS 3 |
| 表单 | react-hook-form + zod |
| 图表 | Recharts |
| Markdown 渲染 | react-markdown + remark-gfm + rehype-raw |
| 代码高亮 | react-syntax-highlighter |

### 1.3 存储方案（与 Ragent 一致）

| 存储 | 技术选型 | 用途 |
|------|---------|------|
| 关系数据库 | **PostgreSQL 16 + pgvector** | 业务数据（用户、会话、知识库、文档、分块）、向量存储备选 |
| 向量数据库 | **Milvus 2.6.6** | 语义向量检索（主） |
| 搜索引擎 | **Elasticsearch + IK 分词器** | BM25 关键词全文检索 |
| 缓存 | **Redis 7.4** | 限流计数、分布式锁、会话缓存 |
| 对象存储 | **RustFS**（S3 兼容） | 上传文档、多模态资产存储 |
| 图数据库 | **Neo4j 5.26 + GDS + APOC** | 知识图谱（LightRAG） |
| 消息队列 | **RocketMQ 5.2.0** | 文档入库异步处理 |

---

## 2. 基础设施部署

### 2.1 Docker Compose 编排

以下为开发环境推荐的 Compose 编排（生产环境需调整安全配置）。

#### 2.1.1 基础中间件栈 (`infra-stack.compose.yaml`)

```yaml
name: ragent-infra

services:
  postgres:
    container_name: ragent-postgres
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: ragent
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d ragent"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    container_name: ragent-redis
    image: redis:7.4-alpine
    command: redis-server --requirepass 123456
    ports:
      - "6379:6379"
    volumes:
      - redisdata:/data
    restart: unless-stopped

  rustfs:
    container_name: ragent-rustfs
    image: rustfs/rustfs:1.0.0-alpha.72
    command:
      - "--address"
      - ":9000"
      - "--console-enable"
      - "--access-key"
      - "rustfsadmin"
      - "--secret-key"
      - "rustfsadmin"
      - "/data"
    environment:
      - RUSTFS_ACCESS_KEY=rustfsadmin
      - RUSTFS_SECRET_KEY=rustfsadmin
      - RUSTFS_CONSOLE_ENABLE=true
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - rustfs-data:/data
    restart: unless-stopped

volumes:
  pgdata:
  redisdata:
  rustfs-data:
```

#### 2.1.2 Milvus 向量数据库 (`milvus-stack.compose.yaml`)

```yaml
name: milvus-stack

services:
  rustfs:
    container_name: milvus-rustfs
    image: rustfs/rustfs:1.0.0-alpha.72
    command:
      - "--address"
      - ":9000"
      - "--console-enable"
      - "--access-key"
      - "rustfsadmin"
      - "--secret-key"
      - "rustfsadmin"
      - "/data"
    ports:
      - "9010:9000"
      - "9011:9001"
    volumes:
      - milvus-rustfs-data:/data

  etcd:
    container_name: milvus-etcd
    image: quay.io/coreos/etcd:v3.5.18
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
    command: >
      etcd
      -advertise-client-urls=http://etcd:2379
      -listen-client-urls http://0.0.0.0:2379
      --data-dir /etcd
    volumes:
      - milvus-etcd-data:/etcd

  standalone:
    container_name: milvus-standalone
    image: milvusdb/milvus:v2.6.6
    command: ["milvus", "run", "standalone"]
    security_opt:
      - seccomp:unconfined
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: rustfs:9000
      MINIO_ACCESS_KEY_ID: rustfsadmin
      MINIO_SECRET_ACCESS_KEY: rustfsadmin
    volumes:
      - milvus-data:/var/lib/milvus
    ports:
      - "19530:19530"
      - "9091:9091"
    depends_on:
      - etcd
      - rustfs

  attu:
    container_name: milvus-attu
    image: zilliz/attu:v2.6.3
    environment:
      MILVUS_URL: milvus-standalone:19530
    ports:
      - "8000:3000"
    depends_on:
      - standalone

volumes:
  milvus-rustfs-data:
  milvus-etcd-data:
  milvus-data:

networks:
  default:
    name: milvus-net
```

> **注意**：RustFS 端口设为 9010/9011 避免与基础栈的 9000/9001 冲突。

#### 2.1.3 Elasticsearch (`es-stack.compose.yaml`)

```yaml
name: es-stack

services:
  elasticsearch:
    container_name: es-standalone
    image: elasticsearch:7.17.25
    environment:
      - discovery.type=single-node
      - "ES_JAVA_OPTS=-Xms1g -Xmx1g"
      - xpack.security.enabled=false
    ports:
      - "9200:9200"
      - "9300:9300"
    volumes:
      - es-data:/usr/share/elasticsearch/data
    restart: unless-stopped

volumes:
  es-data:
```

> ES 启动后需安装 IK 分词器：
> ```bash
> docker exec -it es-standalone bin/elasticsearch-plugin install https://get.infini.cloud/elasticsearch/analysis-ik/7.17.25
> docker restart es-standalone
> ```

#### 2.1.4 RocketMQ (`rocketmq-stack.compose.yaml`)

```yaml
name: rocketmq-stack

services:
  namesrv:
    container_name: rmq-namesrv
    image: apache/rocketmq:5.2.0
    command: sh mqnamesrv
    ports:
      - "9876:9876"
    volumes:
      - rmq-namesrv-data:/home/rocketmq/store

  broker:
    container_name: rmq-broker
    image: apache/rocketmq:5.2.0
    command: sh mqbroker -n namesrv:9876 --enable-proxy
    ports:
      - "10911:10911"
      - "10909:10909"
    environment:
      - NAMESRV_ADDR=namesrv:9876
    depends_on:
      - namesrv
    volumes:
      - rmq-broker-data:/home/rocketmq/store

volumes:
  rmq-namesrv-data:
  rmq-broker-data:
```

#### 2.1.5 LightRAG + Neo4j 知识图谱栈 (`lightrag-neo4j-stack.compose.yaml`)

```yaml
name: lightrag-neo4j-stack

services:
  neo4j:
    container_name: neo4j
    image: neo4j:5.26-enterprise
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: "neo4j/password123"
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_dbms_security_procedures_unrestricted: "apoc.*,gds.*"
      NEO4J_server_memory_heap_max__size: "1G"
      NEO4J_server_memory_pagecache_size: "512m"
    volumes:
      - neo4j-data:/data
      - neo4j-logs:/logs
    healthcheck:
      test: ["CMD-SHELL", "cypher-shell -u neo4j -p password123 'RETURN 1' || exit 1"]
      interval: 15s
      timeout: 10s
      retries: 10
    restart: unless-stopped

  lightrag:
    container_name: lightrag
    image: ghcr.io/hkuds/lightrag:latest
    ports:
      - "9621:9621"
    environment:
      WORKING_DIR: "/app/data/rag_storage"
      LLM_BINDING: "openai"
      LLM_BINDING_HOST: "https://dashscope.aliyuncs.com/compatible-mode/v1"
      LLM_BINDING_API_KEY: "${BAILIAN_API_KEY}"
      LLM_MODEL: "qwen-plus-latest"
      SUMMARY_LANGUAGE: "简体中文"
      EMBEDDING_BINDING: "openai"
      EMBEDDING_BINDING_HOST: "https://api.siliconflow.cn/v1"
      EMBEDDING_BINDING_API_KEY: "${SILICONFLOW_API_KEY}"
      EMBEDDING_MODEL: "Qwen/Qwen3-Embedding-8B"
      EMBEDDING_DIM: "1536"
      LIGHTRAG_GRAPH_STORAGE: "Neo4JStorage"
      LIGHTRAG_KV_STORAGE: "PGKVStorage"
      LIGHTRAG_VECTOR_STORAGE: "PGVectorStorage"
      LIGHTRAG_DOC_STATUS_STORAGE: "PGDocStatusStorage"
      NEO4J_URI: "neo4j://neo4j:7687"
      NEO4J_USERNAME: "neo4j"
      NEO4J_PASSWORD: "password123"
      POSTGRES_HOST: "host.docker.internal"
      POSTGRES_PORT: "5432"
      POSTGRES_USER: "postgres"
      POSTGRES_PASSWORD: "postgres"
      POSTGRES_DATABASE: "ragent"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - lightrag-data:/app/data
    depends_on:
      neo4j:
        condition: service_healthy

volumes:
  neo4j-data:
  neo4j-logs:
  lightrag-data:
```

### 2.2 启动顺序

```bash
# 1. 基础中间件
docker compose -f infra-stack.compose.yaml up -d

# 2. Milvus 向量数据库
docker compose -f milvus-stack.compose.yaml up -d

# 3. Elasticsearch（可选，需关键词搜索时启用）
docker compose -f es-stack.compose.yaml up -d

# 4. RocketMQ（可选，需异步处理时启用）
docker compose -f rocketmq-stack.compose.yaml up -d

# 5. LightRAG + Neo4j（可选，需知识图谱时启用）
docker compose -f lightrag-neo4j-stack.compose.yaml up -d
```

> 开发初期可从 PostgreSQL + Milvus + Redis + RustFS 四个核心组件开始，后续按需扩展 ES、RocketMQ、Neo4j。

---

## 3. 数据库设计

### 3.1 PostgreSQL Schema

> 以下为精简核心表结构，完整建表 DDL 参考 Ragent 项目的 `resources/database/schema_pg.sql`。

#### 用户与会话

```sql
CREATE EXTENSION IF NOT EXISTS vector;

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
```

#### 知识库与文档

```sql
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
```

#### 意图树与查询词映射

```sql
-- 意图树节点表
CREATE TABLE t_intent_node (
    id                    VARCHAR(20) PRIMARY KEY,
    kb_id                 VARCHAR(20),
    intent_code           VARCHAR(64)  NOT NULL,
    name                  VARCHAR(64)  NOT NULL,
    level                 SMALLINT     NOT NULL,  -- 0:DOMAIN 1:CATEGORY 2:TOPIC
    parent_code           VARCHAR(64),
    description           VARCHAR(512),
    examples              TEXT,
    collection_name       VARCHAR(128),
    top_k                 INTEGER,
    prompt_snippet        TEXT,
    prompt_template       TEXT,
    sort_order            INTEGER DEFAULT 0,
    enabled               SMALLINT DEFAULT 1,
    create_by             VARCHAR(20),
    update_by             VARCHAR(20),
    create_time           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted               SMALLINT DEFAULT 0
);

-- 查询词映射表（同义词/术语标准化）
CREATE TABLE t_query_term_mapping (
    id          VARCHAR(20) PRIMARY KEY,
    domain      VARCHAR(64),
    source_term VARCHAR(128) NOT NULL,
    target_term VARCHAR(128) NOT NULL,
    match_type  SMALLINT DEFAULT 1,  -- 1:精确 2:模糊
    priority    INTEGER  DEFAULT 100,
    enabled     SMALLINT DEFAULT 1,
    remark      VARCHAR(255),
    create_by   VARCHAR(20),
    update_by   VARCHAR(20),
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted     SMALLINT DEFAULT 0
);
```

#### RAG 链路追踪

```sql
CREATE TABLE t_rag_trace_run (
    id              VARCHAR(20) PRIMARY KEY,
    trace_id        VARCHAR(64) NOT NULL,
    trace_name      VARCHAR(128),
    conversation_id VARCHAR(20),
    task_id         VARCHAR(20),
    user_id         VARCHAR(20),
    status          VARCHAR(16) DEFAULT 'RUNNING',
    error_message   TEXT,
    start_time      TIMESTAMP,
    end_time        TIMESTAMP,
    duration_ms     INTEGER,
    extra_data      JSONB,
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted         SMALLINT DEFAULT 0
);

CREATE TABLE t_rag_trace_node (
    id              VARCHAR(20) PRIMARY KEY,
    trace_id        VARCHAR(64) NOT NULL,
    node_id         VARCHAR(64) NOT NULL,
    parent_node_id  VARCHAR(64),
    depth           INTEGER,
    node_type       VARCHAR(32),  -- REWRITE / INTENT / SEARCH / FUSION / RERANK / GENERATE
    node_name       VARCHAR(128),
    status          VARCHAR(16) DEFAULT 'RUNNING',
    error_message   TEXT,
    start_time      TIMESTAMP,
    end_time        TIMESTAMP,
    duration_ms     INTEGER,
    extra_data      JSONB,
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted         SMALLINT DEFAULT 0
);
```

#### 示例问题

```sql
CREATE TABLE t_sample_question (
    id          VARCHAR(20) PRIMARY KEY,
    title       VARCHAR(64),
    description VARCHAR(255),
    question    VARCHAR(255) NOT NULL,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted     SMALLINT DEFAULT 0
);
```

### 3.2 Milvus Collection Schema

> 每个知识库对应一个 Milvus Collection，Collection 名为 `rag_{collection_name}`。创建 Collection 的核心参数：

```python
from pymilvus import Collection, CollectionSchema, FieldSchema, DataType

def create_kb_collection(collection_name: str, dim: int = 1536):
    """为知识库创建 Milvus Collection"""
    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=20, is_primary=True),
        FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=20),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=20),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
    ]
    schema = CollectionSchema(fields, description=f"Knowledge base: {collection_name}")
    collection = Collection(name=f"rag_{collection_name}", schema=schema)

    # 创建 IVF_FLAT 索引
    index_params = {
        "metric_type": "COSINE",
        "index_type": "IVF_FLAT",
        "params": {"nlist": 1024}
    }
    collection.create_index(field_name="vector", index_params=index_params)
    collection.load()
```

### 3.3 Elasticsearch 索引 Mapping

```python
ES_INDEX_NAME = "rag_keyword_store"

INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "ik_max_word_analyzer": {"type": "ik_max_word"},
                "ik_smart_analyzer": {"type": "ik_smart"}
            }
        }
    },
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},             # chunk 雪花 ID
            "kb_id": {"type": "keyword"},
            "doc_id": {"type": "keyword"},
            "collection_name": {"type": "keyword"},
            "content": {
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_smart"
            },
            "outline": {                            # 标题路径，如 "API文档 > 认证 > Token"
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_smart"
            },
            "create_time": {"type": "date"}
        }
    }
}
```

---

## 4. 服务端项目结构

```
ragent-server/
├── pyproject.toml              # 项目元数据与依赖（Poetry/Pipenv/uv）
├── .env                        # 环境变量（不提交到 Git）
├── .env.example                # 环境变量模板
├── alembic.ini                 # 数据库迁移配置
├── alembic/
│   └── versions/               # 迁移版本文件
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 应用入口
│   ├── config.py               # 配置管理（pydantic-settings）
│   ├── dependencies.py         # FastAPI 依赖注入
│   │
│   ├── api/                    # API 路由层
│   │   ├── __init__.py
│   │   ├── router.py           # 路由汇总注册
│   │   ├── auth.py             # 认证接口
│   │   ├── conversation.py     # 会话接口
│   │   ├── message.py          # 消息接口
│   │   ├── chat.py             # 问答 SSE 接口（核心）
│   │   ├── knowledge_base.py   # 知识库 CRUD
│   │   ├── knowledge_doc.py    # 文档管理
│   │   ├── knowledge_chunk.py  # 分块管理
│   │   ├── intent.py           # 意图树
│   │   ├── dashboard.py        # 仪表板
│   │   ├── trace.py            # 链路追踪
│   │   └── settings.py         # 系统设置
│   │
│   ├── models/                 # SQLAlchemy ORM 模型
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── user.py
│   │   ├── conversation.py
│   │   ├── message.py
│   │   ├── knowledge_base.py
│   │   ├── knowledge_document.py
│   │   ├── knowledge_chunk.py
│   │   └── intent.py
│   │
│   ├── schemas/                # Pydantic 请求/响应模型
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── conversation.py
│   │   ├── chat.py
│   │   ├── knowledge.py
│   │   └── common.py           # 分页、统一响应等
│   │
│   ├── services/               # 业务逻辑层
│   │   ├── __init__.py
│   │   ├── auth_service.py
│   │   ├── conversation_service.py
│   │   ├── chat_service.py     # 问答编排（核心）
│   │   ├── knowledge_service.py
│   │   ├── intent_service.py
│   │   └── trace_service.py
│   │
│   ├── rag/                    # RAG 核心引擎
│   │   ├── __init__.py
│   │   ├── pipeline.py         # RAG 主流程编排
│   │   ├── rewrite.py          # 问题重写
│   │   ├── intent.py           # 意图识别
│   │   ├── search/
│   │   │   ├── __init__.py
│   │   │   ├── base.py         # 检索通道抽象基类
│   │   │   ├── vector.py       # 向量检索通道 (Milvus)
│   │   │   ├── keyword.py      # 关键词检索通道 (ES BM25)
│   │   │   ├── graph.py        # 图谱检索通道 (LightRAG)
│   │   │   └── web_search.py   # 网络搜索通道（可选）
│   │   ├── postprocess/
│   │   │   ├── __init__.py
│   │   │   ├── dedup.py        # 去重
│   │   │   ├── fusion.py       # RRF 融合排序
│   │   │   └── rerank.py       # Rerank 精排
│   │   └── context.py          # 上下文组装
│   │
│   ├── ingestion/              # 文档入库引擎
│   │   ├── __init__.py
│   │   ├── pipeline.py         # 入库流程编排
│   │   ├── fetcher.py          # 文档获取（文件上传/URL/飞书）
│   │   ├── parser.py           # 文档解析（PDF/Word/Excel/Markdown）
│   │   ├── chunker.py          # 分块策略（固定大小/语义感知/Block-Aware）
│   │   ├── embedder.py         # Embedding 向量化
│   │   ├── indexer.py          # 写入 Milvus + ES
│   │   └── scheduler.py        # 定时刷新调度
│   │
│   ├── llm/                    # LLM 调用封装
│   │   ├── __init__.py
│   │   ├── client.py           # 统一 LLM 客户端（OpenAI 兼容接口）
│   │   ├── router.py           # 模型路由与健康检查
│   │   ├── embedding.py        # Embedding 客户端
│   │   └── reranker.py         # Rerank 客户端
│   │
│   ├── infra/                  # 基础设施层
│   │   ├── __init__.py
│   │   ├── database.py         # PostgreSQL 连接管理
│   │   ├── redis.py            # Redis 客户端
│   │   ├── milvus.py           # Milvus 客户端管理
│   │   ├── elasticsearch.py    # ES 客户端
│   │   ├── oss.py              # S3 对象存储客户端
│   │   ├── mq.py               # RocketMQ 生产者
│   │   └── neo4j.py            # Neo4j 客户端
│   │
│   ├── middleware/              # 中间件
│   │   ├── __init__.py
│   │   ├── auth.py             # 认证中间件
│   │   ├── trace.py            # 链路追踪中间件
│   │   └── rate_limit.py       # 限流中间件
│   │
│   └── utils/                   # 工具函数
│       ├── __init__.py
│       ├── snowflake.py        # 雪花 ID 生成
│       ├── text.py             # 文本处理工具
│       └── token_counter.py    # Token 计数
│
├── tests/
│   ├── conftest.py
│   ├── test_chat.py
│   ├── test_search.py
│   └── test_ingestion.py
│
└── scripts/
    ├── init_db.py              # 初始化数据库
    └── seed_data.py            # 测试数据填充
```

---

## 5. 核心 RAG 链路实现

### 5.1 整体流程

一次问答的完整链路（详见 rag.md）：

```
用户提问 → 问题重写 → 意图识别
    → [向量通道 | 关键词通道 | 图谱通道] 三路并行检索
    → 去重 → RRF 融合 → 截断(前N条) → Rerank 精排 → TopK → LLM 流式生成
```

检索漏斗三段预算：
- **第一阶段**：每通道各召回 `recall_budget`（默认 20）条
- **第二阶段**：RRF 融合后取前 `rerank_candidate_limit`（默认 40）条
- **第三阶段**：Rerank 精排后取前 `default_top_k`（默认 10）条喂给 LLM

### 5.2 核心代码骨架

#### 5.2.1 RAG Pipeline 主流程

```python
# app/rag/pipeline.py

import asyncio
from dataclasses import dataclass, field
from app.rag.search.base import SearchChannel, SearchResult
from app.rag.postprocess.dedup import deduplicate
from app.rag.postprocess.fusion import rrf_fusion
from app.rag.postprocess.rerank import rerank
from app.rag.rewrite import rewrite_query
from app.rag.intent import recognize_intent

@dataclass
class RAGContext:
    question: str
    conversation_id: str | None = None
    history: list[dict] = field(default_factory=list)
    deep_thinking: bool = False

@dataclass
class RAGResult:
    context_chunks: list[dict]   # Top K 精排后的上下文片段
    sources: list[dict]          # 引用来源

class RAGPipeline:
    def __init__(
        self,
        channels: list[SearchChannel],
        config: dict,
    ):
        self.channels = channels
        self.config = config

    async def run(self, ctx: RAGContext) -> RAGResult:
        # 1. 问题重写
        rewritten = await rewrite_query(ctx.question, ctx.history)

        # 2. 意图识别
        intent = await recognize_intent(rewritten)

        # 3. 多路并行检索
        recall_budget = self.config["recall_budget"]
        tasks = [
            channel.search(rewritten, intent, top_k=recall_budget)
            for channel in self.channels
            if channel.enabled
        ]
        channel_results: list[list[SearchResult]] = await asyncio.gather(*tasks)

        # 4. 去重（同一 chunkId 只保留一份）
        all_results = deduplicate(channel_results)

        # 5. RRF 融合排序
        channel_weights = self.config.get("channel_weights", {})
        fused = rrf_fusion(
            channel_results,
            k=self.config.get("rrf_k", 20),
            weights=channel_weights,
        )

        # 6. 截断（送入 Rerank 前的候选池上限）
        candidate_limit = self.config["rerank_candidate_limit"]
        fused = fused[:candidate_limit]

        # 7. Rerank 精排
        if self.config.get("rerank_enabled", True):
            fused = await rerank(ctx.question, fused, top_k=self.config["default_top_k"])

        # 8. 返回 Top K
        return RAGResult(
            context_chunks=fused,
            sources=self._extract_sources(fused),
        )

    def _extract_sources(self, chunks: list[SearchResult]) -> list[dict]:
        seen = set()
        sources = []
        for i, chunk in enumerate(chunks):
            if chunk.doc_id in seen:
                continue
            seen.add(chunk.doc_id)
            sources.append({
                "index": i + 1,
                "docId": chunk.doc_id,
                "docName": chunk.doc_name,
                "excerpt": chunk.content[:200],
            })
        return sources
```

#### 5.2.2 检索通道基类

```python
# app/rag/search/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class SearchResult:
    chunk_id: str
    doc_id: str
    doc_name: str
    kb_id: str
    content: str
    score: float
    channel: str = ""         # "vector" / "keyword" / "graph"
    outline: str = ""         # 标题路径

class SearchChannel(ABC):
    """检索通道基类"""

    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.enabled = enabled

    @abstractmethod
    async def search(
        self, query: str, intent: dict, top_k: int
    ) -> list[SearchResult]:
        """执行检索，返回结果列表"""
        ...
```

#### 5.2.3 向量检索通道

```python
# app/rag/search/vector.py

from app.rag.search.base import SearchChannel, SearchResult
from app.infra.milvus import get_milvus_client
from app.llm.embedding import EmbeddingClient

class VectorSearchChannel(SearchChannel):
    def __init__(self, embedding_client: EmbeddingClient):
        super().__init__(name="vector")
        self.embedding = embedding_client

    async def search(self, query: str, intent: dict, top_k: int) -> list[SearchResult]:
        # 1. 生成查询向量
        vector = await self.embedding.embed_query(query)

        # 2. 确定检索范围（有意图定向时收窄到指定 collection）
        collection_name = intent.get("collection_name") or "rag_default_store"

        # 3. 在 Milvus 中执行向量检索
        from pymilvus import Collection
        collection = Collection(f"rag_{collection_name}")
        collection.load()

        search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}
        results = collection.search(
            data=[vector],
            anns_field="vector",
            param=search_params,
            limit=top_k,
            output_fields=["kb_id", "doc_id", "chunk_index"],
        )

        # 4. 从 PostgreSQL 读取 chunk 内容
        chunk_ids = [hit.id for hit in results[0]]
        chunks = await self._load_chunks(chunk_ids)

        return [
            SearchResult(
                chunk_id=c.id,
                doc_id=c.doc_id,
                doc_name=c.doc_name,
                kb_id=c.kb_id,
                content=c.content,
                score=hit.score,
                channel="vector",
            )
            for hit, c in zip(results[0], chunks)
        ]
```

#### 5.2.4 RRF 融合算法

```python
# app/rag/postprocess/fusion.py

def rrf_fusion(
    channel_results: list[list[SearchResult]],
    k: int = 20,
    weights: dict[str, float] | None = None,
) -> list[SearchResult]:
    """
    Reciprocal Rank Fusion

    公式: score(chunk) = Σ ( weight / (k + rank_in_channel) )

    被多路命中的 chunk 得分叠加，排名自然靠前。
    """
    if weights is None:
        weights = {}

    scores: dict[str, float] = {}        # chunk_id -> 得分
    metadata: dict[str, SearchResult] = {} # chunk_id -> 完整信息

    for results in channel_results:
        for rank, r in enumerate(results, start=1):
            w = weights.get(r.channel, 1.0)
            score = w / (k + rank)
            scores[r.chunk_id] = scores.get(r.chunk_id, 0) + score
            if r.chunk_id not in metadata:
                metadata[r.chunk_id] = r

    # 按得分降序排列
    sorted_ids = sorted(scores, key=scores.get, reverse=True)
    return [
        SearchResult(
            chunk_id=cid,
            doc_id=metadata[cid].doc_id,
            doc_name=metadata[cid].doc_name,
            kb_id=metadata[cid].kb_id,
            content=metadata[cid].content,
            score=scores[cid],
            channel="fusion",
        )
        for cid in sorted_ids
    ]
```

#### 5.2.5 文档分块策略

```python
# app/ingestion/chunker.py

from enum import Enum
from dataclasses import dataclass

class ChunkStrategy(str, Enum):
    FIXED_SIZE = "FIXED_SIZE"           # 固定大小
    STRUCTURE_AWARE = "STRUCTURE_AWARE" # 语义感知（Markdown）
    BLOCK_AWARE = "BLOCK_AWARE"         # 结构化感知

@dataclass
class ChunkConfig:
    chunk_size: int = 512       # 每段字数
    overlap: int = 128          # 重叠字数
    strategy: ChunkStrategy = ChunkStrategy.FIXED_SIZE

class DocumentChunker:
    """文档分块器"""

    def chunk(self, text: str, config: ChunkConfig) -> list[dict]:
        if config.strategy == ChunkStrategy.FIXED_SIZE:
            return self._fixed_size_chunk(text, config)
        elif config.strategy == ChunkStrategy.STRUCTURE_AWARE:
            return self._structure_aware_chunk(text, config)
        else:
            return self._block_aware_chunk(text, config)

    def _fixed_size_chunk(self, text: str, config: ChunkConfig) -> list[dict]:
        """固定大小切分，优先在句号、换行处断句"""
        chunks = []
        start = 0
        index = 0
        while start < len(text):
            end = min(start + config.chunk_size, len(text))
            if end < len(text):
                # 回退到最近的句号或换行
                for sep in ["\n\n", "\n", "。", ". "]:
                    pos = text.rfind(sep, start, end)
                    if pos > start + config.chunk_size // 2:
                        end = pos + len(sep)
                        break
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append({"content": chunk_text, "index": index})
                index += 1
            start = end - config.overlap
        return chunks

    def _structure_aware_chunk(self, text: str, config: ChunkConfig) -> list[dict]:
        """语义感知切分：按 Markdown 标题、代码块、空行边界切"""
        # 实现参考 Ragent 的 StructureAwareTextChunker
        # 识别 # 标题、```代码块```、空行作为自然边界
        ...

    def _block_aware_chunk(self, text: str, config: ChunkConfig) -> list[dict]:
        """Block-Aware：按结构化 Block（表格/代码/列表）类型分发"""
        ...
```

#### 5.2.6 Embedding 向量化

```python
# app/llm/embedding.py

import httpx
from app.config import settings

class EmbeddingClient:
    """统一的 Embedding 客户端，支持多供应商"""

    def __init__(self, model_id: str = "qwen-emb-8b"):
        self.model_id = model_id
        self.config = self._get_model_config(model_id)

    def _get_model_config(self, model_id: str) -> dict:
        # 映射到具体供应商配置
        models = {
            "qwen-emb-8b": {
                "provider": "siliconflow",
                "base_url": "https://api.siliconflow.cn/v1",
                "model": "Qwen/Qwen3-Embedding-8B",
                "api_key": settings.SILICONFLOW_API_KEY,
                "dimension": 1536,
            }
        }
        return models[model_id]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量文本转向量"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.config['base_url']}/embeddings",
                headers={"Authorization": f"Bearer {self.config['api_key']}"},
                json={
                    "model": self.config["model"],
                    "input": texts,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]

    async def embed_query(self, text: str) -> list[float]:
        """单条查询转向量"""
        results = await self.embed_documents([text])
        return results[0]
```

#### 5.2.7 LLM 流式对话

```python
# app/llm/client.py

import json
import httpx
from typing import AsyncIterator

class LLMClient:
    """统一 LLM 客户端，OpenAI 兼容接口"""

    def __init__(self, model_id: str = "qwen-plus"):
        self.model_id = model_id
        self.config = self._get_model_config(model_id)

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """流式对话，逐 token 产出"""
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self.config['base_url']}/chat/completions",
                headers={"Authorization": f"Bearer {self.config['api_key']}"},
                json={
                    "model": self.config["model"],
                    "messages": messages,
                    "temperature": temperature,
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0]["delta"]
                            if "content" in delta and delta["content"]:
                                yield delta["content"]
                            if "reasoning_content" in delta and delta["reasoning_content"]:
                                # 深度思考内容
                                yield f"__THINK__{delta['reasoning_content']}"
                        except (json.JSONDecodeError, KeyError):
                            continue
```

#### 5.2.8 SSE 问答接口

```python
# app/api/chat.py

from fastapi import APIRouter, Request, Query
from fastapi.responses import StreamingResponse
from app.rag.pipeline import RAGPipeline, RAGContext
from app.llm.client import LLMClient
from app.services.chat_service import ChatService
import json
import asyncio

router = APIRouter(prefix="/rag/v3", tags=["chat"])

@router.get("/chat")
async def chat(
    request: Request,
    question: str = Query(...),
    conversation_id: str | None = Query(None),
    deep_thinking: bool = Query(False),
):
    """SSE 流式问答接口"""

    rag_pipeline: RAGPipeline = request.app.state.rag_pipeline
    llm_client: LLMClient = request.app.state.llm_client
    chat_service: ChatService = request.app.state.chat_service

    async def event_stream():
        try:
            # 1. 获取对话历史
            history = []
            if conversation_id:
                history = await chat_service.get_recent_messages(
                    conversation_id, turns=8
                )

            # 2. 保存用户消息
            user_msg_id = await chat_service.save_message(
                conversation_id=conversation_id,
                role="user",
                content=question,
            )

            # 3. RAG 检索
            ctx = RAGContext(
                question=question,
                conversation_id=conversation_id,
                history=history,
                deep_thinking=deep_thinking,
            )
            rag_result = await rag_pipeline.run(ctx)

            # 4. 发送 meta 信息（conversationId、taskId）
            meta = {
                "event": "meta",
                "data": json.dumps({
                    "conversationId": conversation_id,
                    "taskId": user_msg_id,
                }),
            }
            yield f"event: meta\ndata: {json.dumps(meta['data'])}\n\n"

            # 5. 构建 Prompt
            context_text = "\n\n---\n\n".join(
                f"[来源 {i+1}] {c['content']}"
                for i, c in enumerate(rag_result.context_chunks)
            )
            system_prompt = (
                "你是一个智能助手，请根据以下参考资料回答用户问题。"
                "如果参考资料不足以回答问题，请如实告知。\n\n"
                f"参考资料：\n{context_text}"
            )
            messages = [{"role": "system", "content": system_prompt}]
            # 添加历史消息（最近 N 轮）
            for h in history[-16:]:  # 最近 8 轮
                messages.append({"role": h["role"], "content": h["content"]})
            messages.append({"role": "user", "content": question})

            # 6. 流式生成并 SSE 推送
            full_content = ""
            thinking_content = ""

            async for token in llm_client.chat_stream(messages):
                # 检查客户端是否断开
                if await request.is_disconnected():
                    break

                if token.startswith("__THINK__"):
                    think_text = token[9:]
                    thinking_content += think_text
                    yield f"event: message\ndata: {json.dumps({'type': 'think', 'delta': think_text})}\n\n"
                else:
                    full_content += token
                    yield f"event: message\ndata: {json.dumps({'type': 'response', 'delta': token})}\n\n"

            # 7. 保存助手消息
            assistant_msg_id = await chat_service.save_message(
                conversation_id=conversation_id,
                role="assistant",
                content=full_content,
                thinking_content=thinking_content or None,
                sources=rag_result.sources,
            )

            # 8. 发送完成事件
            finish_data = json.dumps({
                "messageId": assistant_msg_id,
                "sources": rag_result.sources,
            })
            yield f"event: finish\ndata: {finish_data}\n\n"
            yield "event: done\ndata: {}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )
```

### 5.3 文档入库流水线

```
上传文档 → Fetch(获取) → Parse(解析) → Chunk(分块) → Embed(向量化) → Index(双写)
```

```python
# app/ingestion/pipeline.py

class IngestionPipeline:
    """文档入库流水线"""

    async def run(self, doc_id: str, kb_id: str, file_path: str):
        # 1. 解析文档
        from app.ingestion.parser import DocumentParser
        parser = DocumentParser()
        parsed_text = await parser.parse(file_path)

        # 2. 分块
        from app.ingestion.chunker import DocumentChunker, ChunkConfig
        chunker = DocumentChunker()
        chunks = chunker.chunk(parsed_text, ChunkConfig())

        # 3. 向量化
        from app.llm.embedding import EmbeddingClient
        embedder = EmbeddingClient()
        texts = [c["content"] for c in chunks]
        vectors = await embedder.embed_documents(texts)

        # 4. 写入 PostgreSQL（分块元数据）
        await self._save_chunks_to_pg(doc_id, kb_id, chunks)

        # 5. 写入 Milvus（向量）
        await self._insert_to_milvus(kb_id, chunks, vectors)

        # 6. 写入 ES（关键词索引，可选）
        if settings.KEYWORD_ENABLED:
            await self._index_to_es(kb_id, chunks)

        # 7. 同步 LightRAG（图谱，可选）
        if settings.GRAPH_ENABLED:
            await self._sync_to_lightrag(kb_id, chunks)
```

---

## 6. API 接口设计

### 6.1 统一响应格式

```json
{
    "code": "0",
    "message": "success",
    "data": { ... }
}
```

### 6.2 核心 API 列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/auth/login` | 用户登录 |
| `GET` | `/api/auth/current` | 获取当前用户 |
| `GET` | `/api/conversations` | 获取会话列表 |
| `POST` | `/api/conversations` | 创建会话 |
| `DELETE` | `/api/conversations/{id}` | 删除会话 |
| `PUT` | `/api/conversations/{id}` | 重命名会话 |
| `GET` | `/api/conversations/{id}/messages` | 获取会话消息列表 |
| `GET` | `/api/rag/v3/chat` | **SSE 流式问答（核心）** |
| `POST` | `/api/rag/v3/stop` | 停止生成 |
| `POST` | `/api/conversations/messages/{id}/feedback` | 提交反馈（点赞/点踩） |
| `DELETE` | `/api/conversations/messages/{id}/feedback` | 取消反馈 |
| `POST` | `/api/conversations/messages/{id}/recommended-questions` | 生成推荐问题 |
| `GET` | `/api/knowledge-base` | 知识库列表 |
| `POST` | `/api/knowledge-base` | 创建知识库 |
| `DELETE` | `/api/knowledge-base/{id}` | 删除知识库 |
| `GET` | `/api/knowledge-base/{kbId}/docs` | 文档列表 |
| `POST` | `/api/knowledge-base/{kbId}/docs/upload` | 上传文档 |
| `DELETE` | `/api/knowledge-base/docs/{docId}` | 删除文档 |
| `GET` | `/api/knowledge-base/docs/{docId}/chunks` | 分块列表 |
| `PUT` | `/api/knowledge-base/docs/{docId}/chunks/{chunkId}` | 编辑分块 |
| `DELETE` | `/api/knowledge-base/docs/{docId}/chunks/{chunkId}` | 删除分块 |
| `GET` | `/api/admin/dashboard/overview` | 仪表板概览 |
| `GET` | `/api/admin/traces` | 链路追踪列表 |
| `GET` | `/api/admin/traces/{traceId}` | 链路追踪详情 |
| `GET` | `/api/intent/tree` | 意图树 |
| `GET` | `/api/sample-questions` | 示例问题列表 |
| `GET` | `/api/query-term-mappings` | 查询词映射 |

### 6.3 前端 API 代理配置

Vite 开发服务器代理配置（`vite.config.ts`）：

```typescript
server: {
  port: 5173,
  proxy: {
    "/api": {
      target: "http://localhost:9090",
      changeOrigin: true,
      secure: false,
    },
  },
}
```

---

## 7. 前端项目结构

```
ragent-frontend/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.cjs
├── postcss.config.cjs
├── .env                          # VITE_API_BASE_URL=/api
├── .eslintrc.cjs
├── .prettierrc
│
├── public/
│   └── favicon.svg
│
└── src/
    ├── main.tsx                  # 应用入口
    ├── App.tsx                   # 根组件
    ├── router.tsx                # 路由配置
    ├── vite-env.d.ts
    │
    ├── styles/
    │   └── globals.css           # Tailwind + 全局样式
    │
    ├── types/
    │   └── index.ts              # 全局 TypeScript 类型
    │
    ├── services/                 # API 服务层
    │   ├── api.ts                # Axios 实例 + 拦截器
    │   ├── authService.ts
    │   ├── sessionService.ts
    │   ├── chatService.ts
    │   ├── knowledgeService.ts
    │   ├── dashboardService.ts
    │   ├── intentTreeService.ts
    │   ├── ragTraceService.ts
    │   └── userService.ts
    │
    ├── stores/                   # Zustand 状态管理
    │   ├── authStore.ts
    │   ├── chatStore.ts          # 核心：聊天状态（消息、流式、会话）
    │   └── themeStore.ts
    │
    ├── hooks/                    # 自定义 Hooks
    │   ├── useAuth.ts
    │   ├── useChat.ts
    │   └── useStreamResponse.ts  # SSE 流式响应解析
    │
    ├── components/
    │   ├── ui/                   # shadcn/ui 基础组件
    │   │   ├── button.tsx
    │   │   ├── input.tsx
    │   │   ├── card.tsx
    │   │   ├── dialog.tsx
    │   │   ├── badge.tsx
    │   │   ├── table.tsx
    │   │   ├── tabs.tsx
    │   │   ├── textarea.tsx
    │   │   ├── select.tsx
    │   │   ├── dropdown-menu.tsx
    │   │   ├── tooltip.tsx
    │   │   └── ...
    │   │
    │   ├── layout/               # 布局组件
    │   │   ├── MainLayout.tsx
    │   │   ├── Header.tsx
    │   │   └── Sidebar.tsx
    │   │
    │   ├── chat/                 # 聊天相关组件
    │   │   ├── ChatInput.tsx     # 输入框（支持深度思考开关）
    │   │   ├── MessageList.tsx   # 消息列表（虚拟滚动）
    │   │   ├── MessageItem.tsx   # 单条消息（用户/助手）
    │   │   ├── MarkdownRenderer.tsx  # Markdown 渲染
    │   │   ├── ThinkingIndicator.tsx # 深度思考动画
    │   │   ├── SourcesPanel.tsx  # 引用来源面板
    │   │   ├── SourcesButton.tsx # 来源按钮
    │   │   ├── FeedbackButtons.tsx   # 点赞/点踩
    │   │   ├── RecommendedQuestions.tsx  # 推荐问题
    │   │   └── WelcomeScreen.tsx # 欢迎页
    │   │
    │   ├── session/              # 会话列表
    │   │   ├── SessionList.tsx
    │   │   └── SessionItem.tsx
    │   │
    │   ├── document/             # 文档预览
    │   │   └── DocumentPreview.tsx
    │   │
    │   └── common/               # 通用组件
    │       ├── Loading.tsx
    │       ├── Avatar.tsx
    │       ├── Toast.tsx
    │       └── ErrorBoundary.tsx
    │
    ├── pages/                    # 页面组件
    │   ├── LoginPage.tsx
    │   ├── ChatPage.tsx          # 问答主界面
    │   ├── DocPreviewPage.tsx
    │   ├── NotFoundPage.tsx
    │   │
    │   └── admin/                # 管理后台
    │       ├── AdminLayout.tsx
    │       ├── dashboard/
    │       │   └── DashboardPage.tsx
    │       ├── knowledge/
    │       │   ├── KnowledgeListPage.tsx      # 知识库列表
    │       │   ├── KnowledgeDocumentsPage.tsx  # 文档管理
    │       │   └── KnowledgeChunksPage.tsx     # 分块管理
    │       ├── intent-tree/
    │       │   ├── IntentTreePage.tsx
    │       │   ├── IntentListPage.tsx
    │       │   └── IntentEditPage.tsx
    │       ├── ingestion/
    │       │   └── IngestionPage.tsx           # 入库流水线
    │       ├── traces/
    │       │   ├── RagTracePage.tsx            # 链路追踪列表
    │       │   └── RagTraceDetailPage.tsx      # 链路追踪详情
    │       ├── settings/
    │       │   └── SystemSettingsPage.tsx
    │       ├── sample-questions/
    │       │   └── SampleQuestionPage.tsx
    │       ├── query-term-mapping/
    │       │   └── QueryTermMappingPage.tsx
    │       └── users/
    │           └── UserListPage.tsx
    │
    ├── lib/                      # 工具库
    │   └── utils.ts              # cn() 合并 Tailwind 类名
    │
    └── utils/                    # 工具函数
        ├── storage.ts            # localStorage 封装
        ├── helpers.ts            # 辅助函数
        ├── time.ts               # 时间格式化
        └── error.ts              # 错误处理
```

---

## 8. 前端核心实现

### 8.1 Axios 实例与拦截器

```typescript
// src/services/api.ts
import axios from "axios";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000,
});

// 请求拦截：自动带 Token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = token;
  }
  return config;
});

// 响应拦截：统一处理 code !== "0" 和 401
api.interceptors.response.use(
  (response) => {
    const payload = response.data;
    if (payload && typeof payload === "object" && "code" in payload) {
      if (payload.code !== "0") {
        if (payload.message?.includes("未登录")) {
          localStorage.clear();
          window.location.href = "/login";
        }
        return Promise.reject(new Error(payload.message));
      }
      return payload.data;  // 直接返回 data
    }
    return payload;
  },
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.clear();
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);
```

### 8.2 SSE 流式响应解析

```typescript
// src/hooks/useStreamResponse.ts

export interface StreamHandlers {
  onMeta?: (payload: { conversationId: string; taskId: string }) => void;
  onMessage?: (payload: { type: string; delta: string }) => void;
  onThinking?: (payload: { type: string; delta: string }) => void;
  onFinish?: (payload: { messageId: string; sources?: SourceRef[] }) => void;
  onDone?: () => void;
  onCancel?: (payload: any) => void;
  onError?: (error: Error) => void;
}

export function createStreamResponse(
  url: string,
  handlers: StreamHandlers,
  signal?: AbortSignal
) {
  const controller = new AbortController();
  const mergedSignal = signal ?? controller.signal;

  const start = async () => {
    const response = await fetch(url, {
      headers: {
        Accept: "text/event-stream",
        Authorization: localStorage.getItem("token") || "",
      },
      signal: mergedSignal,
    });

    if (!response.body) throw new Error("No response body");
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let eventName = "message";
    let dataLines: string[] = [];

    const dispatch = () => {
      if (dataLines.length === 0) return;
      const raw = dataLines.join("\n");
      let payload: any;
      try { payload = JSON.parse(raw); } catch { payload = raw; }

      switch (eventName) {
        case "meta": handlers.onMeta?.(payload); break;
        case "message":
          if (payload?.type === "think") handlers.onThinking?.(payload);
          handlers.onMessage?.(payload);
          break;
        case "finish": handlers.onFinish?.(payload); break;
        case "done": handlers.onDone?.(); break;
        case "cancel": handlers.onCancel?.(payload); break;
        case "error": handlers.onError?.(new Error(payload?.error || "Unknown error")); break;
      }
      eventName = "message";
      dataLines = [];
    };

    while (true) {
      const { value, done } = await reader.read();
      if (done) { dispatch(); break; }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line) { dispatch(); continue; }
        if (line.startsWith("event:")) { eventName = line.slice(6).trim(); continue; }
        if (line.startsWith("data:")) { dataLines.push(line.slice(5).trim()); }
      }
    }
  };

  return { start, cancel: () => controller.abort() };
}
```

### 8.3 ChatStore（Zustand 核心状态）

```typescript
// src/stores/chatStore.ts

import { create } from "zustand";
import type { Message, Session, SourceRef } from "@/types";

interface ChatState {
  sessions: Session[];
  currentSessionId: string | null;
  messages: Message[];
  isLoading: boolean;
  isStreaming: boolean;
  deepThinkingEnabled: boolean;
  streamingMessageId: string | null;
  openedSourceMessageId: string | null;

  // Actions
  fetchSessions: () => Promise<void>;
  selectSession: (id: string) => Promise<void>;
  sendMessage: (content: string) => Promise<void>;
  cancelGeneration: () => void;
  appendStreamContent: (delta: string) => void;
  appendThinkingContent: (delta: string) => void;
  setDeepThinking: (enabled: boolean) => void;
  toggleSourcesPanel: (messageId: string) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  currentSessionId: null,
  messages: [],
  isLoading: false,
  isStreaming: false,
  deepThinkingEnabled: false,
  streamingMessageId: null,
  openedSourceMessageId: null,

  // ... 实现各 Action（详见 Ragent 项目 frontend/src/stores/chatStore.ts）
  sendMessage: async (content) => {
    // 1. 添加用户消息 + 空的助手消息（占位）
    // 2. 调用 createStreamResponse 发起 SSE 连接
    // 3. 注册 onMeta / onMessage / onThinking / onFinish / onDone 回调
    // 4. 回调中更新 messages 状态
  },
  // ...
}));
```

### 8.4 路由设计

```typescript
// src/router.tsx
import { createBrowserRouter } from "react-router-dom";

export const router = createBrowserRouter([
  { path: "/", element: <HomeRedirect /> },
  { path: "/login", element: <LoginPage /> },
  {
    path: "/chat",
    element: <RequireAuth><ChatPage /></RequireAuth>,
  },
  {
    path: "/chat/:sessionId",
    element: <RequireAuth><ChatPage /></RequireAuth>,
  },
  {
    path: "/admin",
    element: <RequireAdmin><AdminLayout /></RequireAdmin>,
    children: [
      { index: true, element: <Navigate to="/admin/dashboard" /> },
      { path: "dashboard", element: <DashboardPage /> },
      { path: "knowledge", element: <KnowledgeListPage /> },
      { path: "knowledge/:kbId", element: <KnowledgeDocumentsPage /> },
      { path: "knowledge/:kbId/docs/:docId", element: <KnowledgeChunksPage /> },
      { path: "intent-tree", element: <IntentTreePage /> },
      { path: "traces", element: <RagTracePage /> },
      { path: "traces/:traceId", element: <RagTraceDetailPage /> },
      { path: "settings", element: <SystemSettingsPage /> },
      { path: "users", element: <UserListPage /> },
    ],
  },
  { path: "*", element: <NotFoundPage /> },
]);
```

---

## 9. 开发路线图

### 第一阶段：基础设施搭建（1-2 周）

- [ ] 使用 Docker Compose 启动 PostgreSQL + Redis + RustFS
- [ ] 启动 Milvus 向量数据库
- [ ] 初始化数据库 Schema（执行 SQL 建表脚本）
- [ ] FastAPI 项目骨架搭建（config、database、models）
- [ ] React 项目骨架搭建（Vite + Tailwind + shadcn/ui）
- [ ] 用户认证（登录/注册/JWT Token）
- [ ] 前后端联调通过

### 第二阶段：核心 RAG 检索链路（2-3 周）

- [ ] Embedding 客户端（SiliconFlow / Ollama）
- [ ] 文档分块器（FIXED_SIZE + STRUCTURE_AWARE 两种策略）
- [ ] 文档入库流水线：Parse → Chunk → Embed → Index(Milvus)
- [ ] 向量检索通道（Milvus 余弦相似度）
- [ ] RRF 融合排序
- [ ] 知识库 CRUD API
- [ ] 文档上传与管理界面（React）

### 第三阶段：问答对话（2 周）

- [ ] LLM 流式对话客户端（百炼 / Ollama）
- [ ] SSE 流式问答 API
- [ ] 问题重写 + 意图识别
- [ ] 前端聊天界面（消息列表、流式渲染、Markdown）
- [ ] 会话管理（创建/删除/重命名/历史消息加载）
- [ ] 深度思考模式
- [ ] 引用来源展示
- [ ] 消息反馈（点赞/点踩）

### 第四阶段：增强检索（1-2 周）

- [ ] Elasticsearch 接入 + IK 分词器
- [ ] BM25 关键词检索通道
- [ ] 双写机制（向量写入时同步写 ES）
- [ ] Rerank 精排（百炼 Rerank API）
- [ ] 多路检索并行调度
- [ ] 通道权重配置

### 第五阶段：知识图谱（1-2 周）

- [ ] Neo4j + LightRAG 部署
- [ ] 图谱检索通道接入
- [ ] 文档入库时同步写图谱
- [ ] 知识图谱可视化（前端管理页）

### 第六阶段：高级特性（2-3 周）

- [ ] Redis 分布式限流
- [ ] 全链路追踪（Trace）
- [ ] URL 文档定时自动刷新（ETag 检测 + Cron）
- [ ] 模型路由与健康检查（多供应商 + 熔断）
- [ ] 管理后台仪表板
- [ ] MCP 工具集成
- [ ] 异步入库（RocketMQ）

### 第七阶段：生产加固（1-2 周）

- [ ] 单元测试与集成测试
- [ ] API 文档（FastAPI 自动生成 OpenAPI）
- [ ] Docker 镜像构建（服务端 + 前端 Nginx）
- [ ] 前端优化（虚拟滚动、懒加载）
- [ ] 安全加固（密码加密、输入校验、CORS）
- [ ] 性能优化（数据库连接池、Milvus 索引调优）

---

## 附录

### A. 环境变量参考 (`.env`)

```bash
# === 服务端 ===
SERVER_PORT=9090
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ragent
REDIS_URL=redis://:123456@127.0.0.1:6379/0
MILVUS_URI=http://localhost:19530

# 对象存储 (RustFS / MinIO)
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=rustfsadmin
S3_SECRET_KEY=rustfsadmin
S3_BUCKET=ragent-sources

# 模型供应商 API Key
SILICONFLOW_API_KEY=sk-xxx
BAILIAN_API_KEY=sk-xxx
AIHUBMIX_API_KEY=sk-xxx

# Elasticsearch（可选）
ES_URIS=http://127.0.0.1:9200
ES_ENABLED=false

# LightRAG（可选）
LIGHTRAG_BASE_URL=http://127.0.0.1:9621
GRAPH_ENABLED=false

# RocketMQ（可选）
ROCKETMQ_NAME_SERVER=127.0.0.1:9876

# === 前端 ===
VITE_API_BASE_URL=/api
VITE_APP_NAME=RAG 智能问答
```

### B. 关键依赖参考 (`pyproject.toml`)

```toml
[project]
name = "ragent-server"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "redis[hiredis]>=5.0",
    "pymilvus>=2.4",
    "elasticsearch[async]>=8.0",
    "boto3>=1.34",         # S3 客户端
    "httpx>=0.27",
    "python-jose[cryptography]>=3.3",  # JWT
    "passlib[bcrypt]>=1.7",
    "apscheduler>=3.10",
    "python-multipart>=0.0.9",
    "sse-starlette>=2.0",
    "neo4j>=5.0",
]
```

### C. 前端关键依赖参考 (`package.json`)

```json
{
  "dependencies": {
    "react": "^18.3",
    "react-dom": "^18.3",
    "react-router-dom": "^6.26",
    "zustand": "^4.5",
    "axios": "^1.7",
    "react-markdown": "^9.0",
    "remark-gfm": "^4.0",
    "rehype-raw": "^7.0",
    "react-syntax-highlighter": "^15.5",
    "lucide-react": "^0.453",
    "sonner": "^1.5",
    "date-fns": "^3.6",
    "react-virtuoso": "^4.9",
    "react-hook-form": "^7.54",
    "zod": "^3.23",
    "@hookform/resolvers": "^3.9",
    "recharts": "^2.13",
    "tailwind-merge": "^2.5",
    "clsx": "^2.1"
  },
  "devDependencies": {
    "typescript": "^5.5",
    "vite": "^5.4",
    "@vitejs/plugin-react": "^4.3",
    "tailwindcss": "^3.4",
    "@tailwindcss/typography": "^0.5",
    "postcss": "^8.4",
    "autoprefixer": "^10.4",
    "eslint": "^8.57",
    "prettier": "^3.3"
  }
}
```

---

> **本文档基于 Ragent (https://github.com/nageoffer/ragent) 项目的架构设计与 RAG 核心原理编写。**  
> Ragent 是一个面向 Agentic RAG 演进的企业级 RAG 平台，采用 Java Spring Boot 3 + React 18 技术栈。本文档在保留其存储方案和 RAG 核心设计的基础上，给出了 Python FastAPI + React 的技术实施路线。
