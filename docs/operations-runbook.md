# flavor-rag 0.0.7 运维手册

本文只覆盖 RAG 数据面，不讨论登录和授权。生产环境的事实源是 PostgreSQL；Milvus、Elasticsearch 和图索引均可由事实源重建。

## 发布前门禁

```bash
cd flavorag-server
uv sync --locked --extra dev
uv run ruff check app tests
uv run pytest -q
uv run alembic upgrade head

cd ../flavorag-frontend
npm ci
npm test
npm run build

docker compose -f ../docker/app.compose.yaml config --quiet
```

空 PostgreSQL 必须能仅通过 `alembic upgrade head` 初始化。应用容器入口会先执行迁移，再启动无 reload 的多 worker Uvicorn。

## 健康检查

- `/api/health/live`：仅表示进程事件循环可响应。
- `/api/health/ready`：检查 PostgreSQL、Redis、Milvus、启用的 Elasticsearch、对象存储和必要模型配置。
- `/metrics`：Prometheus 指标。

负载均衡器只应把流量发给 readiness 为 200 的实例。

## 备份与恢复

建议目标：PostgreSQL RPO ≤ 15 分钟、RTO ≤ 2 小时；对象存储启用版本化并执行跨可用区复制。

```bash
# PostgreSQL 逻辑备份
pg_dump --format=custom --file=flavorag.dump "$DATABASE_URL"

# 恢复到空库
pg_restore --clean --if-exists --no-owner \
  --dbname="$RESTORE_DATABASE_URL" flavorag.dump
alembic upgrade head
```

同时备份 RustFS/MinIO 的 `flavorag-sources` bucket。Milvus 和 Elasticsearch 快照可缩短 RTO，但不作为唯一备份；丢失时从 PostgreSQL active generation 重建。

恢复演练至少每季度一次，必须验证：

1. 文档、active chunk、source object 与 asset object 数量可对账。
2. 为每个知识库执行新的 index generation build。
3. `/api/health/ready` 恢复为 200。
4. 固定 corpus snapshot 的评测任务完成，质量门禁不回退。

## 索引事故

- 维度不匹配：写入会显式失败，禁止删除当前 collection。调用 `POST /api/knowledge-bases/{kb_id}/index-generations` 创建新 generation。
- 新 generation 只有在向量数和维度校验通过后才切换；失败时旧 collection 保持 active。
- reconciliation worker 每 30 分钟对账 PostgreSQL active chunk 与 Milvus，缺失项进入 repair queue，孤儿向量被清理。
- retired/failed collection 默认保留 7 天后删除，可用 `INDEX_RETIRED_RETENTION_DAYS` 调整。

## 跨库 Graph RAG

- `kb_id=*` 只表示当前用户全部可读知识库，不表示绕过租户/ACL 的系统全局范围。
- 全库请求会按知识库数放大 Vector、BM25 和 Graph 查询量，应监控 Graph 通道延迟、超时与 breaker 状态。
- 单库组合图上限为 200 个实体；全库组合图按规范化实体类型分别最多 200 个，查看 `truncatedByType/typeStats` 可定位被截断的类型。`truncated=true` 是正常容量提示，不是图服务错误。
- v0.0.6 之后重新处理的文档会持久化 `tenant_id`、`normalized_name` 和 `CROSS_KB_RELATED`。历史图可在读取时获得严格同名的临时跨库桥；若需要忽略标点/空格的规范化关联，应通过既有摄取/索引修复流程重新处理文档，不要直接批量修改 Neo4j。
- Graph/LightRAG 故障可按 optional channel 降级，但全库请求仍会调度 Graph 通道并在 channel status 中暴露失败。

### v0.0.7 语义关系抽取

- 新增/修改文档会自动从现有 chunks 抽取带证据的 `SEMANTIC_RELATED`；删除文档或知识库沿用 `IndexSyncService` 清理，不需要单独删图。
- 语义抽取失败不会删除基础图，并会进入持久化 Graph repair queue。先查日志事件
  `semantic_graph_enrichment_failed` 和 `t_index_repair_job`，再核对
  `GRAPH_SEMANTIC_API_KEY`（为空时复用主 LLM key）、模型、超时和 provider 限额。
- 弱关系偏多时提高 `GRAPH_SEMANTIC_MIN_CONFIDENCE`；漏边较多时先抽样检查文档表述是否明确，再考虑调整模型。不要直接在 Neo4j 中手工改置信度掩盖抽取问题。
- `--concurrency` 未传时读取 `GRAPH_SEMANTIC_BACKFILL_CONCURRENCY`，默认 2、运行时最大 8。
  生产环境不建议关闭三个严格校验开关；确需放宽时先用 `--limit` 抽样并人工检查证据。
- 历史图不需要重建知识库、重新 chunk 或重新 Embedding：

```bash
cd flavorag-server

# 只统计范围，不调用 LLM、不写图
python -m app.rag.graph.semantic_backfill

# 建议先小范围抽样
python -m app.rag.graph.semantic_backfill --apply --limit 20 --concurrency 2

# 指定知识库；抽样通过后再去掉 limit
python -m app.rag.graph.semantic_backfill --apply --kb-id <kb_id> --concurrency 2
```

- 回填结果中的 `complete/failed/entities/edges/rejected` 必须留档。`failed > 0` 时先按
  `failures[].docId` 重跑指定文档，不要无脑提高并发。模型 provider 的限流通常应把并发维持在 1–2。

## 队列事故

摄取、批量导入、评测和全量索引构建均持久化到 PostgreSQL。worker 使用 `FOR UPDATE SKIP LOCKED` 或 PostgreSQL advisory lock，多副本不会重复领取。

- `RETRY`：指数退避后自动重试。
- `DEAD`/`failed`：达到最大次数，保留错误信息，需要排障后重新发起。
- `RUNNING` 长时间不更新：claim lease 到期后可被其他 worker 重新领取。

禁止通过直接改业务表把失败任务标成成功。

## 观测与告警

Prometheus 规则位于 `docker/observability/flavorag-alerts.yml`，至少告警：

- HTTP 5xx 比例；
- 端到端 RAG P95；
- 空召回突增；
- 摄取积压；
- active chunk 与 Milvus 漂移。

trace 默认只保存哈希和结构化元数据，详细 trace 与评测结果默认保留 30 天。

## 回滚

应用回滚不应回滚或删除 active index generation。数据库 migration 的 downgrade 不是生产回滚手段；应恢复上一应用镜像，并保持向后兼容的新增列。需要切回向量索引时，应通过受控 promotion 操作选择仍保留的 generation，而不是重命名或删除 collection。
