# flavor-rag — 企业级 RAG 智能问答系统

> 版本：v0.0.1 | 状态：生产就绪（68/68 测试全绿）

基于 Python FastAPI + React 技术栈的企业级 RAG (Retrieval-Augmented Generation) 系统。支持多路混合检索、近邻补偿、知识图谱、Agentic RAG、评测体系与全链路追踪。

> 📘 **技术方案详解**：参见 [技术方案文档.md](./技术方案文档.md)，涵盖分块逻辑、召回逻辑、评测体系、链路监控、Graph RAG、Agentic RAG 等核心设计。

## 项目结构

```
flavor-rag/
├── README.md
├── 技术方案文档.md                       # 核心架构详解
├── .env.example
├── .gitignore
│
├── docker/                             # Docker Compose 编排
│   ├── infra-stack.compose.yaml        # PG + Redis + RustFS
│   ├── milvus-stack.compose.yaml       # Milvus 向量数据库
│   ├── es-stack.compose.yaml           # Elasticsearch (可选)
│   ├── rocketmq-stack.compose.yaml     # RocketMQ (可选)
│   ├── lightrag-neo4j-stack.compose.yaml  # LightRAG + Neo4j (可选)
│   └── app.compose.yaml                # 应用镜像
│
├── database/
│   └── schema_pg.sql                   # PostgreSQL 建表脚本
│
├── docs/
│   ├── evaluation/                     # 评测分析报告
│   └── specs/                          # 设计规格说明
│
├── flavorag-server/                    # 服务端 (Python FastAPI)
│   ├── pyproject.toml
│   ├── alembic.ini                     # 数据库迁移
│   ├── run.py / run_dev.py
│   ├── alembic/versions/               # 8 个迁移版本
│   ├── tests/                          # 68 个测试用例
│   └── app/
│       ├── main.py                     # FastAPI 入口
│       ├── config/                     # 配置 + 日志
│       ├── database/                   # 数据库连接 + SQLite Schema
│       ├── models/__init__.py          # 25 个 SQLAlchemy 模型
│       ├── auth/                       # JWT 认证 + 依赖注入
│       ├── api/                        # 15 个 API 模块
│       ├── rag/                        # RAG 核心引擎
│       │   ├── pipeline.py             # 检索主流水线 (重写→意图→多路检索→融合→Rerank)
│       │   ├── search/                 # vector (Milvus) + keyword (ES BM25)
│       │   ├── postprocess/            # fusion (RRF) + reranker (Cross-Encoder/LLM)
│       │   ├── graph/                  # neo4j_store + lightrag_client
│       │   ├── rewrite.py              # 查询改写 (术语映射 + LLM)
│       │   ├── intent.py               # 意图识别 (词法 + LLM)
│       │   ├── governance.py           # 检索治理 (预算/熔断/超时)
│       │   ├── trace.py                # 链路追踪
│       │   ├── model_router.py         # 意图→模型路由
│       │   ├── rate_limiter.py         # Redis 滑动窗口限流
│       │   ├── decompose.py            # 查询分解
│       │   └── recommendations.py      # 推荐问题生成
│       ├── ingestion/                  # 文档入库流水线
│       │   ├── pipeline.py             # 单文档快速入库
│       │   ├── pipeline_engine.py      # DAG 流水线引擎 (6 种节点)
│       │   ├── nodes/                  # fetcher/parser/chunker/enricher/enhancer/indexer
│       │   ├── chunker.py              # 三种分块策略 (Fixed/Semantic/BlockAware)
│       │   ├── parser.py               # 文档解析 (Markdown/PDF/DOCX/XLSX/PPTX/HTML)
│       │   ├── structured.py           # 结构化文档分块
│       │   ├── url_fetcher.py          # URL 安全抓取
│       │   └── pdf/                    # 复杂 PDF 多模态处理 (OCR/VLM/表格)
│       ├── llm/                        # LLM 客户端
│       │   ├── client.py               # 流式 LLM (支持 Bailian/SiliconFlow/AIhubmix + 推理分离)
│       │   └── embedding.py            # Embedding 客户端 (自动维度检测)
│       ├── agent/                      # Agentic RAG
│       │   ├── controlled.py           # 受控 Agent 循环
│       │   ├── planner.py              # LLM 决策下一步行动
│       │   └── rag_agent.py            # 编排检索+工具
│       ├── tools/                      # Agent 可用工具 (SQL / MCP)
│       ├── evaluation/                 # 评测框架 (指标计算 + 质量门禁 + 投资决策)
│       ├── audit/                      # 操作审计 (中间件 + 日志)
│       ├── security/                   # 细粒度 ACL (租户/部门/角色)
│       └── services/                   # 对话管理 / 索引同步 / 定时刷新
│
└── flavorag-frontend/                  # 前端 (React + Vite + TypeScript)
    ├── Dockerfile / nginx.conf
    ├── package.json / vite.config.ts
    ├── tailwind.config.cjs
    └── src/
        ├── pages/                      # 14 个页面 (问答/知识库/管理后台)
        │   ├── ChatPage.tsx            # 流式问答 + 引用溯源
        │   ├── LoginPage.tsx           # 登录注册
        │   └── admin/                  # 管理后台 (Dashboard/评测/追踪/流水线/图谱/权限...)
        ├── components/                 # 可复用组件
        │   └── chat/                   # 消息列表/输入框/来源面板/知识图谱/思考指示器
        ├── services/                   # API 客户端 (auth/chat/knowledge/graph/evaluation...)
        ├── stores/                     # Zustand 状态管理
        ├── hooks/                      # SSE 流式响应 Hook
        └── types/                      # TypeScript 类型定义
```

## 核心能力

| 模块 | 能力 | 状态 |
|------|------|------|
| **分块** | 三种策略：固定窗口 / 语义切分 / 块感知切分 + 复杂 PDF 多模态分块 | ✅ |
| **检索** | 多路并行：Milvus 向量 + ES BM25 关键词 + Neo4j/LightRAG 图谱 → RRF 融合 → 去重 → 近邻补偿（上下文窗口扩展）→ Cross-Encoder Rerank | ✅ |
| **查询理解** | 术语映射（DB驱动） + LLM 改写（消解指代） + 意图分类（层级意图树→知识库路由→模型路由） | ✅ |
| **Graph RAG** | 双层图谱：Neo4j 确定性实体（零 LLM 依赖）+ LightRAG 语义增强 | ✅ |
| **Agentic RAG** | 受控 Agent 循环：检索 → 评估 → 再检索/SQL/MCP 工具 → 最多 4 步 | ✅ |
| **评测** | 8 项检索指标 + 拒绝能力 + ACL 防泄露 + 8 道硬门禁 + 投资决策框架 | ✅ |
| **链路追踪** | 全链路 Trace (改写/意图/检索/融合/Rerank) + 入库耗时追踪 + 管理后台可视化 | ✅ |
| **安全** | JWT 认证 + 租户/部门/角色 ACL + 跨租户防泄露 + 操作审计 + 只读 SQL 白名单 | ✅ |
| **治理** | 检索预算控制 + Circuit Breaker 熔断 + 通道超时 + Redis 滑动窗口限流 | ✅ |
| **定时任务** | 文档定时刷新 (Cron) + 幂等锁 + 内容变化检测 + 索引同步重试 | ✅ |
| **前端** | 流式 SSE 问答 + 来源标亮 + 知识图谱可视化 + 管理后台 14 个页面 | ✅ |

## 快速开始

### 1. 启动基础设施

```bash
# 核心组件 (PG + Redis + RustFS)
docker compose -f docker/infra-stack.compose.yaml up -d

# 向量数据库
docker compose -f docker/milvus-stack.compose.yaml up -d

# 可选组件:
# docker compose -f docker/es-stack.compose.yaml up -d            # Elasticsearch
# docker compose -f docker/lightrag-neo4j-stack.compose.yaml up -d # 知识图谱
```

### 2. 初始化数据库

```bash
# 使用 Alembic 迁移 (推荐)
cd flavorag-server
alembic upgrade head

# 或直接执行建表脚本
docker exec -i flavorag-postgres psql -U postgres -d flavorag < database/schema_pg.sql
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少填写:
#   SILICONFLOW_API_KEY=<your-key>   (或 BAILIAN_API_KEY)
```

### 4. 启动服务端

```bash
cd flavorag-server
pip install -e ".[dev]"
python run.py
# API 文档: http://localhost:9090/docs
```

### 5. 启动前端

```bash
cd flavorag-frontend
npm install
npm run dev
# 访问: http://localhost:5173
```

## 运行测试

```bash
cd flavorag-server
pytest tests/ -v
# 68/68 全绿
```

## 检索链路速览

```
用户问题
  → 查询改写 (术语映射 + LLM消解指代)
  → 意图识别 (层级意图树 → 路由知识库)
  → 三路并行检索 (Milvus向量 + ES BM25 + Neo4j/LightRAG图谱)
  → RRF 融合 (多路结果加权合并)
  → 内容去重 (Jaccard 3-gram)
  → 近邻补偿 (召回命中块前后各2块，补全上下文)
  → Rerank 重排序 (Cross-Encoder 精排)
  → 上下文组装 (多样性优先 + 上下文窗口控制)
  → ACL 权限过滤 → LLM 生成 → 回答 + 来源引用
```

## 技术栈

- **后端**: Python 3.11+ / FastAPI / SQLAlchemy / Alembic
- **前端**: React 18 / Vite / TypeScript / Tailwind CSS / Zustand
- **向量库**: Milvus (IVF_FLAT + Cosine)
- **关键词检索引擎**: Elasticsearch (BM25)
- **图数据库**: Neo4j + LightRAG HTTP API
- **嵌入模型**: Qwen3-Embedding-8B (4096维)
- **Reranker**: Qwen3-Reranker-8B (Cross-Encoder)
- **LLM**: Qwen-Plus / DeepSeek-V3 (支持 Bailian / SiliconFlow / AIhubmix)
- **对象存储**: RustFS (S3 兼容)
- **消息队列**: RocketMQ (可选)
- **缓存**: Redis

## 与 flavor-code 融合

flavor-rag 可作为 flavor-code 的 RAG 后端服务，提供语义代码检索能力。详见 `rag融合.md`。
