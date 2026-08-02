# flavor-rag

> 版本：v0.0.9 · 企业级运营增强版 · 2026-08-02

flavor-rag 是一个面向企业知识库的全链路 RAG 系统，覆盖多格式摄取、版本化索引、混合检索、证据约束生成、引用、长期记忆、离线评测和生产可观测性。本版本不把登录与权限作为企业级结论的一部分；重点是 RAG 数据面的正确性、可恢复性和可运维性。

## 架构

```mermaid
flowchart LR
    U["文件 / URL"] --> V["有界校验与共享源存储"]
    V --> Q["PostgreSQL 持久化任务"]
    Q --> P["Parse / OCR / VLM / Semantic Chunk"]
    P --> G["Pending document generation"]
    G --> M["Milvus required"]
    G --> E["Elasticsearch optional/required"]
    G --> N["Neo4j 基础图 + 证据语义图 + LightRAG"]
    M --> A["原子激活 generation"]
    E --> A
    N --> A
    A --> R["单库 / 全部可读知识库"]
    R --> X["Vector + BM25 + Graph + HyDE 按库 fan-out"]
    X --> F["RRF / Dedup / ACL / Rerank / Budget"]
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
- Prometheus UI：`http://localhost:19090`
- Grafana：`http://localhost:13000`（默认账号/密码：`admin` / `admin`）
- Jaeger UI：`http://localhost:16687`
- OpenTelemetry OTLP HTTP：`http://localhost:14318`

启动可观测性组件：

```bash
docker compose -f docker/observability-stack.compose.yaml up -d
```

Compose 中 `SOURCE_STORAGE_BACKEND=s3`，源文件保存在 RustFS。仅本地开发可使用默认的 `local`。

### 本地开发

```bash
cd flavorag-server
uv sync --locked --extra dev --extra otel
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
LLM_CONTEXT_WINDOW_TOKENS=32768
LLM_MAX_OUTPUT_TOKENS=2048

GRAPH_ENABLED=true
GRAPH_SEMANTIC_ENABLED=true
GRAPH_SEMANTIC_MODEL=qwen-plus-latest
GRAPH_SEMANTIC_TEMPERATURE=0
GRAPH_SEMANTIC_MAX_TOKENS=2048
GRAPH_SEMANTIC_MAX_INPUT_CHARS=4000
GRAPH_SEMANTIC_BATCH_CHUNKS=6
GRAPH_SEMANTIC_MAX_ENTITIES_PER_BATCH=12
GRAPH_SEMANTIC_MAX_RELATIONSHIPS_PER_BATCH=16
GRAPH_SEMANTIC_TIMEOUT_SEC=45
GRAPH_SEMANTIC_MIN_CONFIDENCE=0.70
GRAPH_SEMANTIC_MIN_EVIDENCE_CHARS=8
GRAPH_SEMANTIC_MAX_EVIDENCE_CHARS=600
GRAPH_SEMANTIC_REQUIRE_ENDPOINTS_IN_EVIDENCE=true
GRAPH_SEMANTIC_REJECT_NEGATIVE_STORES=true
GRAPH_SEMANTIC_VALIDATE_PART_OF_DIRECTION=true
GRAPH_SEMANTIC_PROVIDER_FALLBACK_ENABLED=true
GRAPH_SEMANTIC_BACKFILL_CONCURRENCY=2
```

`EMBEDDING_MODEL` 是新建知识库的服务端默认权威，前端未显式选型时不会重复发送模型名；历史简写 `qwen3-embedding-8b` 会在服务端规范化。`EMBEDDING_DIM` 只是默认值。创建知识库时会实际探测模型输出维度并写入 index generation；已有 collection 不会因配置变化被删除。

`GRAPH_SEMANTIC_MODEL` 建议使用支持稳定 JSON 输出的低温轻量模型。它只负责抽取候选，
服务端仍会做证据和 schema 校验。提高 `GRAPH_SEMANTIC_MIN_CONFIDENCE` 会减少边但提升精度；
生产环境建议先抽样核验，再从 `0.70` 调整。长文档会按字符数和 chunk 数分批，避免一次
prompt 过大；显式语义模型不可用时会尝试已配置且端点匹配的 HyDE/Mem0 轻量模型。语义
抽取属于增强通道，所有兼容模型都失败时基础图仍可用。

调参时可以按目的理解，不需要一次改完：

| 目标 | 参数 | 默认值 | 影响 |
|---|---|---:|---|
| 更快/更省 | `MAX_INPUT_CHARS`、`BATCH_CHUNKS`、`MAX_TOKENS` | 4000 / 6 / 2048 | 越小单次越快，但批次数可能增加 |
| 控制图密度 | `MAX_ENTITIES_PER_BATCH`、`MAX_RELATIONSHIPS_PER_BATCH` | 12 / 16 | 越小越偏向少而精 |
| 控制可信度 | `MIN_CONFIDENCE`、`MIN_EVIDENCE_CHARS` | 0.70 / 8 | 越高/越长越严格 |
| 控制随机性 | `TEMPERATURE` | 0 | 图谱抽取建议保持 0 |
| 严格证据 | `REQUIRE_ENDPOINTS_IN_EVIDENCE` | true | 要求证据原句同时出现关系两端 |
| 防方向误判 | `REJECT_NEGATIVE_STORES`、`VALIDATE_PART_OF_DIRECTION` | true / true | 拦截"删除却标存储"和 `PART_OF` 反向 |
| 故障降级 | `PROVIDER_FALLBACK_ENABLED` | true | 主模型失败时尝试已配置的兼容轻量模型 |
| 历史回填 | `BACKFILL_CONCURRENCY` | 2 | 最大运行时仍限制为 8 |

完整配置及默认值以 [.env.example](.env.example) 为准。长度、温度、置信度、token 和并发
在服务端还有安全边界，超范围配置会被收敛到安全值。

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

**非破坏性索引（v0.0.5）**：embedding 模型或维度不匹配会显式失败，不再自动删除 collection。全量重建写入新的物理 generation，校验完成后原子切换 active collection。摄取使用稳定 idempotency key；新 chunk 和 PDF asset 先进入 `PENDING`，Milvus 及 required channel 成功后才激活。失败时旧 generation 继续服务。

**持久化后台任务（v0.0.5）**：单文档摄取、批量导入、评测和索引重建均由 PostgreSQL 队列驱动，可在进程崩溃后恢复；多副本通过 `SKIP LOCKED` 或 advisory lock 协调。自动对账定时对比 PostgreSQL active chunk 与 Milvus，缺失文档进入 repair queue，孤儿向量被清理。

**有界文档处理（v0.0.5）**：限制文件大小、批量数、PDF 页数、压缩包展开大小/文件数/压缩比、图片像素和 OCR/VLM 并发；阻塞 PDF 解析与同步 SDK 调用移出事件循环。

### 检索

- `kb_id=*` 表示"全部可读知识库"；缺省/`null` 仍保留旧的首库选择语义，不与全库范围混用。
- 全库模式会解析为 `{kb_id, active collection, embedding model}` 范围集合，各检索通道按范围 fan-out；最终 ACL 过滤失败时 fail closed。

**跨库检索（v0.0.6）**：知识库选择器新增"全部知识库"，服务端只展开当前用户有读取权限的知识库，向量、BM25、Neo4j 和 LightRAG 按库并发检索，融合后由 PostgreSQL 按精确 KB 集合与文档 ACL 再次过滤。

**跨库公平召回（v0.0.8）**：多库搜索结果按 KB Interleave 交错排列，消除位置偏差；Rerank 后执行 Per-KB 配额保障（最低 `kb_min_quota` 条），不足时从 Fallback Pool 补入。所有新增逻辑均有 `len(scopes) > 1` 守卫，单知识库查询走原有路径。

- 全库模式强制 Graph RAG，不能通过前端开关或直接 API 请求关闭。
- 图谱 API 默认且最多返回 200 个实体；`truncated=true` 仅表示实际匹配实体超过 200。
- Query rewrite、intent、推测向量检索和可选 HyDE 并行执行；rewrite/intent 超时或异常时自动降级回退（v0.0.8）。
- Vector、BM25、Graph 多通道有独立超时、熔断、状态和指标。
- RRF、vector、reranker 使用独立阈值。
- 最终上下文预算按 token 计算，并为 system、history、memory、profile 和输出保留空间。
- PostgreSQL 会再次过滤非 `ACTIVE` chunk，避免外部索引短暂不一致造成脏读。

**跨库知识关联（v0.0.6）**：Neo4j 实体新增 `tenant_id`、`normalized_name` 与 `cross_linkable`，过滤通用噪声后，同租户、不同知识库的规范化同名实体通过代表性 `CROSS_KB_RELATED` 关联。

**可信语义知识图谱（v0.0.7）**：三层混合图谱——基础层（标题、代码标识符共现，零 LLM）、语义层（轻量 LLM 抽取 `USES`/`DEPENDS_ON`/`IMPLEMENTS` 等白名单关系，逐条原文证据校验）、跨库层（同租户同名实体锚点，`JSON`/`API` 等通用词禁作名称桥）。增删改自动维护图结构，语义 provider 失败时基础图继续可用。

**来源归因标注（v0.0.8）**：上下文每段证据标注所属知识库和文档名，LLM 可在回答中正确归因。

### 生成与引用

- system prompt 明确把 evidence、memory、profile 视为不可信数据。
- 输入、history、evidence 和输出都有 token/长度边界与总超时。
- 引用只允许 `[N]` 指向本次实际 sources；错误或无支持的引用不会被当作有效引用。
- 服务端 finish 事件包含 `fullAnswer`，前端用它覆盖增量流，保证 UI 与数据库一致。
- 对话后记忆只抽取用户明确表达的事实，不把 assistant 推测写成用户事实。

**Prompt 与引用安全（v0.0.5）**：检索证据、记忆和画像均按不可信数据注入；最终引用会校验索引和证据重叠，流式内容、finish 事件和持久化答案保持一致。

## 评测与门禁

`POST /api/admin/evaluation/run` 只创建持久化任务；前端轮询运行状态，不占用长 HTTP 请求。指标包括：

- Precision、Recall、Hit Rate、MRR、MAP、NDCG、document recall；
- answerability、refusal precision/recall/F1、ACL leakage、stability；
- groundedness、completeness、answer relevance、correctness；
- citation precision、claim coverage；
- prompt-injection canary safety；
- P50/P95/P99 latency 和关键指标 95% CI。

门禁要求至少 30 个启用案例，并要求 reference answer 覆盖率。当前
`knowledge-archive-golden-v1.jsonl` 覆盖 6 个归档知识库、36 篇文档和
2,174 个切片；每条案例显式绑定知识库、文档、切片、corpus snapshot
和 document generation。评测页可选择单库切片，也可选择“全部知识库”
运行跨库评测。语料版本不匹配时 API 会明确拒绝运行，不会用失效 chunk ID
静默打分。

**企业评测（v0.0.5）**：评测绑定 corpus snapshot、document/index generation、embedding 模型和 prompt 版本，同时计算 Retrieval 与 Answer 指标、95% 置信区间、最小样本门禁和生成阶段 prompt-injection canary。

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

**生产部署（v0.0.5）**：空 PostgreSQL 可直接 `alembic upgrade head`；容器使用锁定依赖、自动迁移、无 reload 多 worker、liveness/readiness、Prometheus 告警和 Grafana 面板。

## 版本历史

| 版本 | 日期 | 主题 | 详细设计 |
|---|---|---|---|
| v0.0.9 | 2026-08-02 | 企业级运营增强：Worker 进程分离 + 语义缓存 + Token 配额 + 质量闭环 + 文档审批 + 表格 QA + 图谱交互 + 版本 Diff + 对话分析 | [技术方案文档](技术方案文档.md) |
| v0.0.8 | 2026-07-28 | 跨库公平召回：KB Interleave + Per-KB 配额 + Fallback Pool + 来源归因 + Rewrite/Intent 降级 | [技术方案文档](技术方案文档.md) |
| v0.0.7 | 2026-07-27 | 可信语义知识图谱：三层混合图谱 + 原文证据校验 + 增量维护 + 原地回填 | [技术方案文档](技术方案文档.md) |
| v0.0.6 | 2026-07-25 | 跨知识库 Graph RAG：全库检索 + 跨库实体桥 + 强制 Graph + 分类型知识星图 | [SDD](docs/specs/cross-knowledge-base-graph-rag.md) |
| v0.0.5 | 2026-07-20 | 企业级 RAG 数据面加固：非破坏索引 + 一致性摄取 + 持久化任务 + 企业评测 | [SDD](docs/0.0.5-enterprise-rag-sdd.md) |

部署与故障处理见 [运维手册](docs/operations-runbook.md)，完整实现说明见 [技术方案文档](技术方案文档.md)。

## 许可证

项目未声明开源许可证；在补充 LICENSE 前，请按内部项目管理。
