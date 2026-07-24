# flavor-code × flavor-rag 融合方案

> 分析日期：2026-07-24 | flavor-code v1.0.2 | flavor-rag v0.1.0

---

## 1. 两个项目的定位

| 维度 | flavor-code | flavor-rag |
|------|-------------|------------|
| **角色** | CLI/Desktop 编码 Agent | 企业级 RAG 知识库服务 |
| **技术栈** | TypeScript + Electron + React 19 | Python FastAPI + React 18 |
| **用户** | 开发者（终端/桌面） | 企业用户（Web 浏览器） |
| **核心能力** | 代码理解、文件操作、工具调用、多模型对话 | 文档检索、语义搜索、知识图谱、流式问答 |
| **部署方式** | npm 全局安装 / Electron 桌面应用 | Docker Compose 服务集群 |
| **存储** | 本地文件系统 (.flavor/) | PostgreSQL + Milvus + ES + Redis + Neo4j |

## 2. 为什么需要融合

### 2.1 flavor-code 的痛点 → flavor-rag 的解法

flavor-code 在 `RAG.md` 中明确指出了 4 个需要 RAG 的切入点：

| flavor-code 痛点 | flavor-rag 提供的解决方案 |
|------------------|--------------------------|
| **代码语义搜索**: Grep 只能做文本匹配，无法理解"认证逻辑在哪"这类语义查询 | **向量检索通道 (Milvus)**: 代码片段 embedding → 语义相似度检索 |
| **文档/知识库 RAG**: 无法理解项目文档、README、设计文档 | **文档入库流水线**: Parse → Chunk → Embed → Index，支持多格式文档 |
| **长期记忆增强**: Jaccard 文本相似度无法捕获语义等价 | **多路检索 + Rerank**: 向量 + BM25 + 图谱，精排融合 |
| **跨会话历史**: 只能恢复最近一次会话 | **会话历史索引**: 压缩摘要入向量库，支持语义检索历史 |

### 2.2 flavor-rag 的增益 → flavor-code 的补充

| flavor-rag 能力 | 对 flavor-code 的价值 |
|----------------|----------------------|
| **可视化管理后台 (React)**: 知识库、文档、分块、链路追踪 | flavor-code 作为纯 CLI 工具缺少可视化管理界面 |
| **多知识库管理**: 创建/配置/删除知识库，独立 Collection | 允许为不同项目创建独立代码索引库 |
| **全链路追踪 (Trace)**: 检索→重写→召回→融合→生成 全流程可观测 | 帮助调试 Agent 的 RAG 检索质量 |
| **意图识别 + 查询重写**: 将模糊查询改写为精确检索语句 | 提升 Agent 代码搜索的召回准确率 |

## 3. 融合架构

```
┌─────────────────────────────────────────────────────────┐
│                    flavor-code (Agent)                   │
│                                                         │
│  ContextManager        RAG Tools         Memory System   │
│  ┌──────────────┐    ┌──────────┐    ┌──────────────┐  │
│  │ pinnedMsgs() │    │RagSearch │    │retrieval.ts  │  │
│  │ +RAG Context │    │  Tool    │    │+vector rank  │  │
│  └──────┬───────┘    └────┬─────┘    └──────┬───────┘  │
│         │                 │                  │          │
│         └─────────────────┼──────────────────┘          │
│                           │ HTTP/SSE                    │
└───────────────────────────┼─────────────────────────────┘
                            │
              ┌─────────────▼─────────────┐
              │    flavor-rag API Gateway  │
              │    (FastAPI :9090)         │
              │                           │
              │  /api/rag/v3/chat  (SSE)  │
              │  /api/rag/v3/search        │
              │  /api/knowledge-base/*     │
              │  /api/admin/traces/*       │
              └─────────────┬─────────────┘
                            │
    ┌───────────────────────┼───────────────────────┐
    │                       │                       │
┌───▼────┐  ┌────────▼──┐  ├──────────┐  ┌──────▼──┐
│Milvus  │  │Elasticsearch│  │PostgreSQL│  │  Neo4j  │
│向量检索 │  │BM25 关键词  │  │业务数据  │  │知识图谱 │
└────────┘  └────────────┘  └──────────┘  └─────────┘
```

### 3.1 两种集成模式

#### 模式 A：Embedded（轻量级，推荐起步）

flavor-code 自带一个简化的本地 RAG 索引（如 flavor-code `RAG.md` 描述的 `src/rag/` 模块），但当需要更强能力时，可配置指向 flavor-rag 服务。

```json
// flavor-code 项目根目录 .flavor/config.json
{
  "rag": {
    "mode": "remote",
    "remoteUrl": "http://localhost:9090",
    "apiKey": "flavor-code-xxx-token",
    "defaultKbId": "kb_project_xxx"
  }
}
```

#### 模式 B：Gateway（企业级）

flavor-rag 作为所有 flavor-code 实例的共享 RAG 后端，提供统一的检索、索引、追踪服务。多个开发者共享同一个知识库。

```
开发者A ── flavor-code ──┐
开发者B ── flavor-code ──┼── flavor-rag ── 共享基础设施
开发者C ── flavor-code ──┘
```

## 4. 具体融合策略

### 4.1 共享基础设施

flavor-rag 的 Docker Compose 编排天然为 flavor-code 提供所需的基础组件：

| flavor-code 需求 | flavor-rag 已有 |
|-----------------|----------------|
| Embedding API | Milvus + Embedding 服务 (SiliconFlow/Ollama) |
| 向量存储 | Milvus 2.6.6 (flavor-code 原方案用 JSON 文件) |
| 关键词检索 | Elasticsearch + IK 分词器 (BM25) |
| 缓存 | Redis 7.4 |
| 对象存储 | RustFS (S3 兼容) |

**关键决策**: flavor-code `RAG.md` 推荐用 JSON 文件存储向量以避免外部依赖。但 flavor-rag 的 Milvus 提供了更高的检索精度和规模扩展能力。建议：
- flavor-code 独立运行时：使用本地 JSON 文件索引（零依赖）
- flavor-code + flavor-rag 联合：升级到 Milvus 向量检索

### 4.2 flavor-code RAG 模块对接 flavor-rag API

flavor-code 的 `src/rag/` 模块可以抽取一个 `EmbeddingProvider` 接口：

```typescript
// flavor-code src/rag/embedder.ts 增加远程模式
interface EmbeddingProvider {
  embed(texts: string[]): Promise<Float32Array[]>;
  embedQuery(query: string): Promise<Float32Array>;
}

class FlavorRagEmbeddingProvider implements EmbeddingProvider {
  private baseUrl: string;
  private token: string;

  constructor(config: { baseUrl: string; token: string }) {
    this.baseUrl = config.baseUrl;
    this.token = config.token;
  }

  async embedQuery(query: string): Promise<Float32Array> {
    const response = await fetch(`${this.baseUrl}/api/rag/v3/search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.token}`,
      },
      body: JSON.stringify({ query, topK: 5, returnVectors: true }),
    });
    const data = await response.json();
    return new Float32Array(data.embedding);
  }
}
```

### 4.3 flavor-code 工具集成

flavor-code 的 `RagSearch` 工具可以直接调用 flavor-rag：

```typescript
// flavor-code src/rag/tool.ts — 增加远程模式
async function ragSearch(query: string): Promise<RagChunk[]> {
  if (config.rag.mode === 'remote') {
    const resp = await fetch(`${config.rag.remoteUrl}/api/rag/v3/search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${config.rag.apiKey}`,
      },
      body: JSON.stringify({
        query,
        kbId: config.rag.defaultKbId,
        topK: 5,
        channels: ['vector', 'keyword'],
      }),
    });
    return (await resp.json()).chunks;
  }
  // 本地模式：使用内嵌 retriever
  return localRetriever.query(query);
}
```

### 4.4 管理界面复用

flavor-code 作为 CLI 工具缺少可视化的知识库管理能力。flavor-rag 的 React 前端天然补充：

- **知识库管理**: 创建项目专属知识库，上传代码索引
- **文档预览**: 查看代码片段的分块详情
- **链路追踪**: 查看每次检索的完整链路（问题重写→向量召回→BM25召回→融合排序→LLM生成）
- **意图树管理**: 配置项目的查询意图分类

## 5. 实施优先级

```
优先级 1 (即刻): 共享基础设施
  ├── PostgreSQL + pgvector（两个项目共用）
  ├── Redis（两个项目共用）
  └── RustFS（文档/代码片段存储）

优先级 2 (短期): flavor-code → flavor-rag 远程检索
  ├── flavor-rag 提供 /api/rag/v3/search API
  ├── flavor-code src/rag/embedder.ts 增加 FlavorRagEmbeddingProvider
  └── flavor-code config.json 增加 rag.remoteUrl 配置

优先级 3 (中期): 代码库索引自动化
  ├── flavor-code /init 时自动同步代码索引到 flavor-rag
  ├── 增量更新（Git hook 触发）
  └── 项目级知识库隔离

优先级 4 (长期): 深度集成
  ├── flavor-code 对话历史导入 flavor-rag 长期记忆
  ├── 跨项目代码搜索
  └── MCP 工具链打通
```

## 6. 数据流示例

### 场景：开发者用 flavor-code 搜索"认证流程"

```
1. flavor-code Agent 收到用户查询 "解释这个项目的认证流程"
2. Agent 调用 RagSearch 工具
3. RagSearch → flavor-rag API: POST /api/rag/v3/search
   { query: "认证流程", kbId: "kb_flavor_code", channels: ["vector", "keyword"] }
4. flavor-rag 执行完整 RAG 管线:
   - 问题重写: "认证流程" → "OAuth JWT Token 认证鉴权"
   - 意图识别: { intent: "code_search", collection: "kb_flavor_code" }
   - 向量检索: Milvus → Top 10 向量相似片段
   - 关键词检索: ES BM25 → Top 10 关键词匹配片段
   - RRF 融合排序 → Top 10
   - Rerank 精排 → Top 5
5. flavor-rag 返回:
   {
     chunks: [
       { file: "src/auth/oauth.ts:45-128", content: "...", score: 0.92 },
       { file: "src/auth/store.ts:12-89", content: "...", score: 0.87 },
       ...
     ],
     traceId: "trace_xxx"
   }
6. Agent 将片段注入 ContextManager，直接回答用户问题
7. 从 8-12 轮降为 2-3 轮，token 节省 60%+
```

## 7. 配置约定

### flavor-code .flavor/config.json 增加 rag 配置

```json
{
  "rag": {
    "mode": "local",
    "remote": {
      "url": "http://localhost:9090",
      "token": "${FLAVOR_RAG_TOKEN}",
      "defaultKbId": null
    },
    "local": {
      "indexDir": ".flavor/rag/index",
      "embedder": "openai:text-embedding-3-small"
    },
    "injection": {
      "enabled": true,
      "topK": 5,
      "minScore": 0.65,
      "maxChars": 4000
    }
  }
}
```

### flavor-rag .env 增加 flavor-code 集成配置

```bash
# flavor-code 集成
FLAVOR_CODE_INTEGRATION_ENABLED=true
FLAVOR_CODE_API_TOKENS=token1,token2
```

## 8. 风险与注意事项

| 风险 | 缓解 |
|------|------|
| 网络延迟影响 Agent 响应速度 | flavor-rag 本地部署（同机 Docker），延迟 <50ms；超时回退本地模式 |
| flavor-rag 宕机导致 Agent 不可用 | graceful fallback：远程调用失败时自动切换到本地 JSON 模式 |
| 代码隐私泄漏到共享服务 | 每个 flavor-code 项目使用独立知识库 + Token 鉴权 |
| 索引同步延迟 | 增量更新 + /init 手动强制重建 |

---

> **结论**: flavor-rag 是 flavor-code 的理想 RAG 后端。两者的融合是 **互补增强** 而非替换关系：flavor-code 保持轻量零依赖的本地能力，通过可选配置升级到 flavor-rag 获得企业级检索精度。优先从"共享基础设施 + 远程检索 API"切入，逐步深化集成。
