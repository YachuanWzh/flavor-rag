# flavor-rag

> 版本：v0.0.5 · 企业级 RAG 数据面加固版 · 2026-07-28

flavor-rag 是一个面向企业知识库的全链路 RAG 系统，覆盖多格式摄取、版本化索引、混合检索、证据约束生成、引用、长期记忆、离线评测和生产可观测性。本版本不把登录与权限作为企业级结论的一部分；重点是 RAG 数据面的正确性、可恢复性和可运维性。

## v0.0.5 关键变化

- 非破坏性索引：embedding 模型或维度不匹配会显式失败，不再自动删除 collection。全量重建写入新的物理 generation，校验完成后原子切换 active collection。
- 一致性摄取：摄取使用稳定 idempotency key；新 chunk 和 PDF asset 先进入 `PENDING`，Milvus 及 required channel 成功后才激活。失败时旧 generation 继续服务。
- 持久化后台任务：单文档摄取、批量导入、评测和索引重建均由 PostgreSQL 队列驱动，可在进程崩溃后恢复；多副本通过 `SKIP LOCKED` 或 advisory lock 协调。
- 自动对账修复：定时对比 PostgreSQL active chunk 与 Milvus，缺失文档进入 repair queue，孤儿向量被清理；optional channel 失败可见且指数退避。
- 检索正确性：修复推测检索跨 query/collection 误复用、HyDE 丢失、冻结预算对象修改、通道异常伪装为空结果和不同分数域共用阈值等问题。
- Prompt 与引用安全：检索证据、记忆和画像均按不可信数据注入；最终引用会校验索引和证据重叠，流式内容、finish 事件和持久化答案保持一致。
- 有界文档处理：限制文件大小、批量数、PDF 页数、压缩包展开大小/文件数/压缩比、图片像素和 OCR/VLM 并发；阻塞 PDF 解析与同步 SDK 调用移出事件循环。
- 企业评测：评测绑定 corpus snapshot、document/index generation、embedding 模型和 prompt 版本，同时计算 Retrieval 与 Answer 指标、95% 置信区间、最小样本门禁和生成阶段 prompt-injection canary。
- 生产部署：空 PostgreSQL 可直接 `alembic upgrade head`；容器使用锁定依赖、自动迁移、无 reload 多 worker、liveness/readiness、Prometheus 告警和 Grafana 面板。

详细设计见 [0.0.5 SDD](docs/0.0.5-enterprise-rag-sdd.md)，部署与故障处理见 [运维手册](docs/operations-runbook.md)，完整实现说明见 [技术方案文档](技术方案文档.md)。

## 架构

```mermaid
flowchart LR
    U["文件 / URL"] --> V["有界校验与共享源存储"]
    V --> Q["PostgreSQL 持久化任务"]
    Q --> P["Parse / OCR / VLM / Semantic Chunk"]
    P --> G["Pending document generation"]
    G --> M["Milvus required"]
    G --> E["Elasticsearch optional/required"]
    G --> N["Neo4j / LightRAG optional/required"]
    M --> A["原子激活 generation"]
    E --> A
    N --> A
    A --> R["Vector + BM25 + Graph + HyDE"]
    R --> F["RRF / Dedup / Rerank / Budget"]
    F --> L["不可信证据约束生成"]
    L --> C["引用校验 + SSE + 持久化"]
    C --> EV["离线评测与质量门禁"]
    A --> RC["对账 / Repair / Retention"]
```

PostgreSQL 是业务事实源。Milvus、Elasticsearch 和图索引是可重建投影；任何外部索引失败都不能提前激活 PostgreSQL generation。

## 技术栈

- 后端：Python 3.11+、FastAPI、SQLAlchemy Async、Alembic
- 数据：PostgreSQL/pgvector、Redis
- 检索：Milvus 2.x、Elasticsearch 8.x、Neo4j/LightRAG
- 文件：RustFS/MinIO S3、pdfplumber、pypdf、OCR/VLM
- 模型：OpenAI-compatible LLM、Embedding、Reranker
- 前端：React 18、TypeScript、Vite、Zustand
- 可观测性：Prometheus、Grafana、OpenTelemetry/Jaeger

Python 依赖由 `flavorag-server/uv.lock` 锁定，前端依赖由 `package-lock.json` 锁定。

## 快速启动

### Docker Compose

```bash
copy .env.example .env
# 填写模型 API Key；生产环境务必替换示例密码
docker compose -f docker/app.compose.yaml up -d --build
```

服务：

- 前端：`http://localhost`
- API/OpenAPI：`http://localhost:9090/docs`
- liveness：`http://localhost:9090/api/health/live`
- readiness：`http://localhost:9090/api/health/ready`
- Prometheus metrics：`http://localhost:9090/metrics`

Compose 中 `SOURCE_STORAGE_BACKEND=s3`，源文件保存在 RustFS。仅本地开发可使用默认的 `local`。

### 本地开发

```bash
cd flavorag-server
uv sync --locked --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 9090

cd ../flavorag-frontend
npm ci
npm run dev
```

## 配置要点

复制 `.env.example` 后至少确认：

```dotenv
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/flavorag
REDIS_URL=redis://:password@127.0.0.1:6379/0
MILVUS_URI=http://127.0.0.1:19530
ES_URIS=http://127.0.0.1:9200
S3_ENDPOINT=http://127.0.0.1:9000
SOURCE_STORAGE_BACKEND=s3

EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
EMBEDDING_DIM=4096
LLM_MODEL=qwen-plus-latest
LLM_CONTEXT_WINDOW_TOKENS=8192
LLM_MAX_OUTPUT_TOKENS=2048
```

`EMBEDDING_DIM` 只是默认值。创建知识库时会实际探测模型输出维度并写入 index generation；已有 collection 不会因配置变化被删除。

## RAG 数据面

### 摄取

支持 PDF、DOCX、XLSX、PPTX、Markdown、文本、HTML、JSON、CSV 和常见图片。PDF 支持布局块、表格、跨页表格、图片 asset、OCR 和可选 VLM 描述；语义分块使用实际 embedding 相邻相似度。

状态流：

```text
QUEUED → RUNNING → SUCCESS
                 ↘ RETRY → DEAD

PENDING generation → required indexes success → ACTIVE
                   ↘ failure → old ACTIVE remains available
```

### 检索

- Query rewrite、intent、推测向量检索和可选 HyDE 并行执行。
- Vector、BM25、Graph 多通道有独立超时、熔断、状态和指标。
- RRF、vector、reranker 使用独立阈值。
- 最终上下文预算按 token 计算，并为 system、history、memory、profile 和输出保留空间。
- PostgreSQL 会再次过滤非 `ACTIVE` chunk，避免外部索引短暂不一致造成脏读。

### 生成与引用

- system prompt 明确把 evidence、memory、profile 视为不可信数据。
- 输入、history、evidence 和输出都有 token/长度边界与总超时。
- 引用只允许 `[N]` 指向本次实际 sources；错误或无支持的引用不会被当作有效引用。
- 服务端 finish 事件包含 `fullAnswer`，前端用它覆盖增量流，保证 UI 与数据库一致。
- 对话后记忆只抽取用户明确表达的事实，不把 assistant 推测写成用户事实。

## 评测与门禁

`POST /api/admin/evaluation/run` 只创建持久化任务；前端轮询运行状态，不占用长 HTTP 请求。指标包括：

- Precision、Recall、Hit Rate、MRR、MAP、NDCG、document recall；
- answerability、refusal precision/recall/F1、ACL leakage、stability；
- groundedness、completeness、answer relevance、correctness；
- citation precision、claim coverage；
- prompt-injection canary safety；
- P50/P95/P99 latency 和关键指标 95% CI。

门禁要求至少 30 个启用案例，并要求 reference answer 覆盖率。当前示例数据集绑定固定示例文档；选择其他 corpus 时 API 会明确拒绝运行，而不是用失效 chunk ID 静默打分。生产使用前应维护组织自己的版本化 golden set。

## 验证

```bash
cd flavorag-server
uv run ruff check app tests
uv run python -m compileall -q app
uv run pytest -q

cd ../flavorag-frontend
npm run build

docker compose -f ../docker/app.compose.yaml config --quiet
```

CI 还会在空 PostgreSQL 上执行完整 Alembic migration，并构建前后端容器。

## 生产注意事项

- 生产必须设置真实模型凭据，否则 readiness 为 503。
- 不要手工删除 active Milvus collection；通过 index generation API 重建。
- 不要把本地 `uploads` 当作多副本共享源存储。
- 对 PostgreSQL 和对象存储执行备份；外部索引应视为可重建投影。
- `TRACE_STORE_CONTENT=false` 为推荐默认值，按保留策略清理 trace 和评测明细。
- 示例 Compose 是单机拓扑；跨可用区高可用需要使用托管或集群化 PostgreSQL、Redis、Milvus、Elasticsearch 和 S3。

## 许可证

项目未声明开源许可证；在补充 LICENSE 前，请按内部项目管理。
