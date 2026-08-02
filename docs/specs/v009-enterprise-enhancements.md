# flavor-rag v0.0.9 企业级增强规格

Status: implementation target
Date: 2026-08-02

## 0. 范围

本增量覆盖四个运营层特性和四个差异化特性：

| 编号 | 特性 | 类别 |
|------|------|------|
| F1 | Worker 进程分离 + 分布式调度锁 | 运营 (#3) |
| F2 | 语义缓存与 Token 成本控制 | 运营 (#6) |
| F3 | 检索质量运营闭环 | 运营 (#7) |
| F4 | 文档准入审批流 | 运营 (#8) |
| F5 | 表格精确问答 (Table QA) | 差异化 |
| F6 | 知识图谱交互式子图展开 | 差异化 |
| F7 | 文档版本 Diff | 差异化 |
| F8 | 对话分析仪表盘 | 差异化 |

所有特性共享以下不变量：
- 多租户隔离：所有新表和查询携带 `tenant_id`
- 幂等性：重试不产生副作用
- 失败可见：新功能故障不影响主 RAG 链路

---

## F1. Worker 进程分离 + 分布式调度锁

### 目标

将 12 个 in-process 后台任务拆分为可独立部署的 worker 进程，API 进程无状态化。
多副本部署时不产生重复执行。

### 设计

#### F1.1 Worker 注册表

新增 `app/worker/registry.py`：

```python
WORKER_REGISTRY: dict[str, WorkerSpec] = {
    "ingestion": WorkerSpec(cls="app.services.ingestion_jobs.IngestionJobWorker", ...),
    "evaluation": WorkerSpec(cls="app.services.evaluation_jobs.EvaluationJobWorker", ...),
    "retention": WorkerSpec(cls="app.services.retention.RetentionWorker", ...),
    "reconciliation": WorkerSpec(cls="app.services.index_reconciliation.IndexReconciliationWorker", ...),
    "repair": WorkerSpec(cls="app.services.index_repair.IndexRepairWorker", ...),
    "index_sync": WorkerSpec(cls="app.services.index_sync.IndexSyncRetryScheduler", ...),
    "watchdog": WorkerSpec(cls="app.services.ingestion_watchdog.IngestionWatchdog", ...),
    "batch_import": WorkerSpec(cls="app.services.batch_import.BatchImportWorker", ...),
    "url_refresh": WorkerSpec(cls="app.services.url_refresh_scheduler.URLRefreshScheduler", ...),
    "doc_schedule": WorkerSpec(cls="app.services.schedule.scheduler.DocumentScheduleScheduler", ...),
    "profile": WorkerSpec(cls="app.memory.profile_scheduler.ProfileScheduler", ...),
    "index_build": WorkerSpec(cls="app.services.index_lifecycle.IndexBuildWorker", ...),
}
```

#### F1.2 独立入口

新增 `flavorag-server/run_worker.py`：

```bash
# 运行全部 worker（等效于当前 in-process）
uv run python run_worker.py --all

# 只运行指定 worker
uv run python run_worker.py --workers ingestion,evaluation,retention
```

#### F1.3 分布式调度锁

扩展现有 `ScheduleLockManager` 为通用 `DistributedLock`：

- PostgreSQL advisory lock（生产）/ SQLite 本地 set（开发）
- 每个 worker 启动时获取全局 worker-claim lock，防止同一 worker 类型多实例
- 锁 TTL 60s，持有者每 30s 续约，进程崩溃后自动过期
- `WORKER_MODE=embedded|standalone`：embedded 保持当前 lifespan 行为（默认），standalone 不启动 worker

#### F1.4 API 进程配置

`settings.worker_mode: str = "embedded"` — `standalone` 时 lifespan 跳过所有 worker 启动。

### 测试

- 单元测试：registry 解析、worker 过滤、锁竞争（模拟两个实例）
- 集成测试：standalone 模式下 API 不启动 worker；embedded 模式保持兼容

---

## F2. 语义缓存与 Token 成本控制

### 目标

高频相似问题命中缓存直接返回，减少 LLM 调用；每租户 token 预算硬限制。

### 设计

#### F2.1 语义缓存

新增 `app/rag/semantic_cache.py`：

```python
class SemanticCache:
    """Embedding-similarity answer cache backed by Redis."""
    
    async def get(self, query_embedding: list[float], tenant_id: str, kb_scope: str) -> CachedAnswer | None
    async def put(self, query_embedding: list[float], tenant_id: str, kb_scope: str, answer: CachedAnswer) -> None
    async def invalidate_kb(self, tenant_id: str, kb_id: str) -> None
```

- 存储：Redis sorted set + hash（key = `sem_cache:{tenant}:{kb_scope}:{hash[:12]}`）
- 匹配：查询 embedding 与缓存 embedding 的 cosine 相似度 ≥ `SEMANTIC_CACHE_THRESHOLD`（默认 0.96）
- TTL：`SEMANTIC_CACHE_TTL_SEC`（默认 3600）
- 失效：知识库索引 generation 切换时调用 `invalidate_kb`
- 开关：`SEMANTIC_CACHE_ENABLED=false`（默认关闭）

#### F2.2 Token 配额

新增 `app/rag/quota.py`：

```python
class TokenQuota:
    """Per-tenant daily token budget enforcement via Redis atomic counter."""
    
    async def check(self, tenant_id: str) -> QuotaStatus  # allowed / remaining / limit
    async def record(self, tenant_id: str, prompt_tokens: int, completion_tokens: int) -> None
```

- 配额表 `t_token_quota`：tenant_id, daily_limit, current_usage, reset_date
- Redis key `quota:{tenant}:{date}` 做原子 incr，每日过期
- 超限返回 429 + 剩余重置时间
- 配置：`TOKEN_QUOTA_ENABLED=false`, `TOKEN_QUOTA_DAILY_DEFAULT=1000000`

#### F2.3 集成点

在 `chat.py` 的 event_stream 开始前：
1. 检查 token 配额 → 超限拒绝
2. 计算 query embedding → 查语义缓存 → 命中则直接 SSE 返回（标记 `cached=true`）
3. 未命中走正常 RAG → 生成后写缓存 + 记录 token 消耗

### 测试

- 单元测试：相似度阈值边界、TTL 过期、KB 失效、配额计数与重置
- 集成测试：缓存命中跳过 LLM、配额超限 429

---

## F3. 检索质量运营闭环

### 目标

用户反馈 → 自动标注 → 评测集扩充 → 超参建议的完整闭环。
零结果/低分查询自动聚类，驱动知识库补充。

### 设计

#### F3.1 反馈驱动评测集

扩展现有 `EvaluationDatasetCase` 生命周期：

- 负反馈（vote=-1）自动标记 `needs_review` + `bad_case`（已有）
- 新增：连续 N 个负反馈的同一 KB 触发 `quality_alert`
- 管理员在评测页面可一键将 bad_case 提升为 golden（已有 promote_to_golden）
- 新增：`auto_promote_threshold` — 正反馈 ≥ 3 且无负反馈的 case 自动提升为 golden

#### F3.2 零结果查询聚类

新增 `app/evaluation/query_clusters.py`：

```python
class QueryClusterAnalyzer:
    """Cluster zero/low-result queries to identify knowledge gaps."""
    
    async def analyze(self, tenant_id: str, days: int = 7) -> list[QueryCluster]
```

- 数据源：`t_rag_trace_run` 中 `final_count=0` 或 `rejection_reason IS NOT NULL` 的记录
- 聚类：基于 query embedding 的 DBSCAN（eps=0.3, min_samples=3）
- 输出：每个 cluster 的代表问题、频次、涉及 KB
- API：`GET /api/admin/evaluation/query-gaps?days=7`

#### F3.3 超参调优建议

新增 `app/evaluation/tuning.py`：

```python
def suggest_hyperparams(run_results: list[EvaluationRun]) -> list[TuningSuggestion]
```

- 基于评测 run 历史对比：Recall@K 下降 → 建议增大 `RETRIEVAL_PER_CHANNEL_TOP_K`
- 拒答率过高 → 建议降低 `RETRIEVAL_RRF_MIN_SCORE`
- 延迟 P95 超标 → 建议降低 `RETRIEVAL_MAX_CANDIDATES`
- API：`GET /api/admin/evaluation/tuning-suggestions`

### 测试

- 单元测试：聚类边界（空数据、单点、噪声）、调优建议规则
- 集成测试：反馈 → case 状态变更 → 聚类 API 返回

---

## F4. 文档准入审批流

### 目标

文档上传后不立即索引，需经审批人确认后才进入摄取队列。

### 设计

#### F4.1 审批状态机

`KnowledgeDocument.status` 扩展：

```
UPLOADED → PENDING_REVIEW → APPROVED → QUEUED → RUNNING → SUCCESS
                          ↘ REJECTED
```

- `APPROVAL_ENABLED=false`（默认关闭，关闭时上传直接进入 QUEUED）
- 审批人：KB 的 WRITE/ADMIN 权限持有者
- 批量上传同样进入审批队列

#### F4.2 数据模型

新增 `t_document_approval` 表：

| 列 | 类型 | 说明 |
|---|---|---|
| id | String(20) | PK |
| tenant_id | String(64) | |
| doc_id | String(20) | FK → t_knowledge_document |
| kb_id | String(20) | |
| status | String(16) | pending/approved/rejected |
| reviewer_id | String(20) | |
| review_comment | Text | |
| submitted_by | String(20) | |
| create_time | DateTime | |
| review_time | DateTime | |

#### F4.3 API

- `POST /api/knowledge-base/{kb_id}/documents/{doc_id}/approve` — 审批通过，触发摄取
- `POST /api/knowledge-base/{kb_id}/documents/{doc_id}/reject` — 拒绝
- `GET /api/knowledge-base/{kb_id}/approvals?status=pending` — 待审批列表
- 前端：知识库详情页新增"待审批"标签页

### 测试

- 单元测试：状态机转换合法性、权限校验
- 集成测试：审批通过后文档进入摄取、拒绝后不可检索

---

## F5. 表格精确问答 (Table QA)

### 目标

对提取的结构化表格做精确单元格级回答，而非仅依赖 chunk 文本。

### 设计

#### F5.1 表格存储

`KnowledgeChunk.block_type = "table"` 的 chunk 已有 `metadata_json` 存储表格数据。
规范化为：

```json
{
  "table": {
    "headers": ["列A", "列B", "列C"],
    "rows": [["值1", "值2", "值3"], ...],
    "source_page": 3
  }
}
```

#### F5.2 表格检索增强

新增 `app/rag/table_qa.py`：

```python
class TableQAEnhancer:
    """Detect table-oriented queries and extract precise cell answers."""
    
    def is_table_query(self, question: str) -> bool
    def extract_answer(self, question: str, tables: list[dict]) -> TableAnswer | None
```

- 识别：包含"多少"、"第几行"、"总计"、"最大/最小"、列名引用等模式
- 提取：基于表头匹配 + 行过滤 + 聚合（sum/max/min/count/avg）
- 降级：无法精确回答时回退到普通 RAG 生成

#### F5.3 集成

在 RAG pipeline 的 fusion 阶段后：
- 如果 top-K 结果中 block_type=table 占比 ≥ 50% 且 is_table_query=true
- 尝试 TableQAEnhancer.extract_answer
- 成功则在 SSE 中发送 `event: table_answer` 附加精确结果

### 测试

- 单元测试：表格查询识别、单元格提取、聚合计算、边界（空表、缺列）
- 集成测试：pipeline 中表格路径触发与降级

---

## F6. 知识图谱交互式子图展开

### 目标

前端图谱面板支持点击节点展开邻居子图，逐步探索。

### 设计

#### F6.1 后端 API

扩展现有 `GET /api/rag/v3/graph`：

- 新增参数 `expand_from: str | None` — 以此实体 ID 为中心展开
- 新增参数 `exclude_ids: str | None` — 逗号分隔的已展示节点 ID（避免重复）
- 返回增加 `expandable: bool` 字段（节点是否有未展示的邻居）

新增 `GET /api/rag/v3/graph/neighbors?node_id=xxx&depth=1&limit=20`：
- 返回指定节点的直接邻居 + 连接边
- 权限同 graph_view

#### F6.2 前端交互

`KnowledgeGraphPanel.tsx` 扩展：
- 节点双击 → 调用 neighbors API → 增量添加节点/边到画布
- 新节点有进入动画（fade-in）
- 节点右键菜单："展开邻居"、"聚焦此实体"、"隐藏"
- 展开历史栈：支持"收回上一步"

### 测试

- 后端单元测试：neighbors API 权限、depth 限制、exclude 去重
- 前端单元测试：增量合并逻辑（`knowledgeGraphUtils.test.ts` 扩展）

---

## F7. 文档版本 Diff

### 目标

同一文档重新上传后，展示新旧版本的文本差异。

### 设计

#### F7.1 版本记录

新增 `t_document_version` 表：

| 列 | 类型 | 说明 |
|---|---|---|
| id | String(20) | PK |
| tenant_id | String(64) | |
| doc_id | String(20) | |
| version_no | Integer | 递增版本号 |
| content_hash | String(64) | |
| chunk_count | Integer | |
| file_size | BigInteger | |
| uploaded_by | String(20) | |
| create_time | DateTime | |
| diff_summary | JSON | {added_chunks, removed_chunks, modified_chunks} |

- 文档重处理/重上传时自动创建新 version 记录
- `diff_summary` 在摄取完成后异步计算

#### F7.2 Diff 计算

新增 `app/services/document_diff.py`：

```python
def compute_chunk_diff(old_chunks: list[str], new_chunks: list[str]) -> DiffSummary
```

- 基于 content_hash 的集合差异（added/removed）
- 相似 chunk（hash 不同但相似度 > 0.8）标记为 modified
- 返回 unified diff 格式（前 500 字符）

#### F7.3 API

- `GET /api/knowledge-base/{kb_id}/documents/{doc_id}/versions` — 版本列表
- `GET /api/knowledge-base/{kb_id}/documents/{doc_id}/diff?v1=1&v2=2` — 两版本对比

### 测试

- 单元测试：diff 计算（纯新增、纯删除、修改、空）
- 集成测试：重上传触发版本记录

---

## F8. 对话分析仪表盘

### 目标

管理后台展示热门话题、满意度趋势、会话深度等运营指标。

### 设计

#### F8.1 聚合 API

新增 `GET /api/admin/analytics/conversations`：

```json
{
  "period": "7d",
  "totalConversations": 128,
  "totalMessages": 892,
  "avgTurnsPerConversation": 6.9,
  "satisfactionTrend": [{"date": "2026-07-27", "positive": 12, "negative": 3}],
  "topTopics": [{"topic": "部署配置", "count": 34, "sampleQuestion": "..."}],
  "peakHours": [{"hour": 10, "count": 45}],
  "avgResponseMs": 3200,
  "refusalRate": 0.08,
  "cacheHitRate": 0.12
}
```

- 数据源：t_conversation, t_message, t_message_feedback, t_rag_trace_run
- topTopics：基于 t_rag_trace_run.query 的 TF-IDF 关键词聚类（轻量，不依赖外部服务）
- 时间粒度：`period` 参数支持 1d/7d/30d

#### F8.2 前端页面

新增 `flavorag-frontend/src/pages/admin/AnalyticsPage.tsx`：
- 折线图：满意度趋势（7/30天）
- 柱状图：每小时提问分布
- 词云/列表：热门话题 Top 10
- 数字卡片：总会话、平均轮次、拒答率、缓存命中率
- 路由：`/admin/analytics`

### 测试

- 单元测试：聚合计算（空数据、跨时区）
- 前端：组件渲染测试

---

## 实现顺序与依赖

```
F1 (Worker 分离)     ← 无依赖，基础设施
F2 (语义缓存+配额)   ← 无依赖
F4 (文档审批)        ← 无依赖
F7 (版本 Diff)       ← 无依赖
F3 (质量闭环)        ← 依赖 F2 的 cacheHitRate 指标（可选）
F5 (表格 QA)         ← 无依赖
F6 (图谱交互)        ← 无依赖
F8 (对话分析)        ← 依赖 F2 的缓存命中率（可选）
```

每个特性遵循 TDD：RED → GREEN → REFACTOR → commit。

## 验收标准

1. `uv run pytest -q` 全部通过
2. `npm run build` 前端构建通过
3. `uv run ruff check app tests` 无 error
4. `WORKER_MODE=standalone` 时 API 进程不启动任何 worker
5. 语义缓存命中时 SSE 返回 `cached: true` 且不调用 LLM
6. 配额超限时返回 HTTP 429
7. 审批关闭时行为与当前完全一致（向后兼容）
8. 所有新 API 执行 tenant_id 过滤
