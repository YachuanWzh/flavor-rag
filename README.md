# flavor-rag

> 版本：v0.0.7 · 可信语义知识图谱版 · 2026-07-28

flavor-rag 是一个面向企业知识库的全链路 RAG 系统，覆盖多格式摄取、版本化索引、混合检索、证据约束生成、引用、长期记忆、离线评测和生产可观测性。本版本不把登录与权限作为企业级结论的一部分；重点是 RAG 数据面的正确性、可恢复性和可运维性。

## v0.0.7 关键变化

以前的跨库边主要回答“两个库里有没有同名实体”。这很稳定，但 `JSON`、`API`
这类通用词即使同时出现，也不代表两个知识库真的有关。v0.0.7 改为三层混合图谱：

- **基础层（不调用 LLM）**：标题、代码标识符和同一 chunk 共现关系，保证模型不可用时仍有图。
- **语义层（轻量 LLM）**：从原文抽取 `USES`、`DEPENDS_ON`、`IMPLEMENTS` 等白名单关系。每条边必须带原文证据、chunk ID、置信度、模型和 prompt 版本；实体或证据不在原文、关系类型越界、置信度低于阈值都会被拒绝。
- **跨库层（高精度对齐）**：只把同租户、不同库中的有效同名实体作为跨库锚点。`JSON`、`API`、`HTTP`、`XML`、`YAML` 等通用词明确禁止生成名称型跨库桥，避免“都提过一个常见词”造成假关联。
- **增量维护**：新建或修改文档时自动替换该文档的旧语义边；删除文档时清理其图节点并重建受影响的跨库桥；删除知识库沿用逐文档清理。新增知识库不需要额外操作，第一篇文档入库时自然进入图谱。
- **失败可恢复**：语义 provider 临时失败不会回滚已写好的基础图；系统会创建持久化 Graph repair job，按退避策略自动重跑。
- **历史数据原地升级**：不用新建知识库，也不用重新 chunk/Embedding。名称桥继续使用 `python -m app.rag.graph.backfill --apply`；语义关系先用 `python -m app.rag.graph.semantic_backfill` 预览，再加 `--apply` 从现有 active chunks 抽取。
- **看得见证据**：知识星图选中实体后会展示关系类型、置信度和支持该关系的原文，方便人工判断这条边是否可信。
- **一遍过门禁**：后端 `221 passed`，前端 `12 passed`，TypeScript 与 Vite 生产构建通过；真实数据验证了 `flavor-code → Harness → huamulan-agent` 的语义边 + 跨库锚点路径。

```mermaid
flowchart LR
    D["新增 / 修改文档"] --> C["现有 chunks"]
    C --> B["规则基础图"]
    C --> L["轻量 LLM 关系抽取"]
    L --> V{"原文证据 + 类型白名单 + 置信度"}
    V -->|通过| S["SEMANTIC_RELATED"]
    V -->|拒绝| X["不写入"]
    B --> A["有效实体跨库对齐"]
    S --> A
    A --> G["可追溯知识星图"]
    R["删除文档 / 知识库"] --> K["删除所属节点和边并局部重建跨库桥"]
```

该方案采用业界常见的“结构化抽取 + 实体消歧 + 证据来源 + 增量更新”组合，而不是让
LLM 自由生成整张图。设计参考
[Microsoft GraphRAG 方法](https://microsoft.github.io/graphrag/index/methods/)、
[GraphRAG 输出模型](https://microsoft.github.io/graphrag/index/outputs/)和
[Neo4j GraphRAG Knowledge Graph Builder](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_kg_builder.html)。

## v0.0.6 关键变化

- **跨库检索**：知识库选择器新增“全部知识库”，显式使用 `kb_id=*`；服务端只展开当前用户有读取权限的知识库，向量、BM25、Neo4j 和 LightRAG 按库并发检索，融合后由 PostgreSQL 按精确 KB 集合与文档 ACL 再次过滤。
- **强制 Graph RAG**：选择“全部知识库”后前端自动开启并锁定 Graph RAG；服务端也会忽略伪造的 `graph_rag=false`，确保跨库请求始终调度图谱通道。
- **跨库知识关联**：Neo4j 实体新增 `tenant_id`、`normalized_name` 与 `cross_linkable`，过滤通用噪声后，同租户、不同知识库的规范化同名实体通过代表性 `CROSS_KB_RELATED` 关联；历史图可原地回填，后续文档增删改自动维护受影响的跨库桥。
- **分类型知识星图**：单库最多 200 实体，全库按实体类型分别最多 200；支持“知识库 × 类型”语义缩放聚合、可视域与 overscan 裁剪、最多 420 个详情节点绘制预算、画布拖拽和指针锚定缩放。
- **Graph 召回动效**：Graph 通道实际召回证据后，前端从查询匹配实体出发高亮连通路径，显示节点呼吸与边光流；`prefers-reduced-motion` 下自动降级为静态高亮。
- **知识库创建修复**：前端不再硬编码无效的 Embedding 简写；服务端以 `EMBEDDING_MODEL` 为默认权威，并兼容旧值 `qwen3-embedding-8b`，统一规范化为 SiliconFlow 可识别的 `Qwen/Qwen3-Embedding-8B`。
- **SDD + TDD**：新增跨库 Graph RAG、分类型容量、视域渲染与知识库创建契约测试；后端全量 `207 passed`，前端 `12 passed`、TypeScript、生产构建及真实 333 节点全库视觉 QA 通过。

详细设计见 [跨知识库 Graph RAG SDD](docs/specs/cross-knowledge-base-graph-rag.md)，部署与故障处理见 [运维手册](docs/operations-runbook.md)，完整实现说明见 [技术方案文档](技术方案文档.md)。

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

0.0.5 的详细设计见 [0.0.5 SDD](docs/0.0.5-enterprise-rag-sdd.md)。

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
LLM_CONTEXT_WINDOW_TOKENS=8192
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
| 防方向误判 | `REJECT_NEGATIVE_STORES`、`VALIDATE_PART_OF_DIRECTION` | true / true | 拦截“删除却标存储”和 `PART_OF` 反向 |
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

### 检索

- `kb_id=*` 表示“全部可读知识库”；缺省/`null` 仍保留旧的首库选择语义，不与全库范围混用。
- 全库模式会解析为 `{kb_id, active collection, embedding model}` 范围集合，各检索通道按范围 fan-out；最终 ACL 过滤失败时 fail closed。
- 全库模式强制 Graph RAG，不能通过前端开关或直接 API 请求关闭。
- 图谱 API 默认且最多返回 200 个实体；`truncated=true` 仅表示实际匹配实体超过 200。
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
