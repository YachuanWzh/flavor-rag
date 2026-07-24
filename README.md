# flavor-rag — 企业级 RAG 智能问答系统

基于 Python FastAPI + React 技术栈的企业级 RAG (Retrieval-Augmented Generation) 系统。

## 项目结构

```
flavor-rag/
├── README.md                           # 项目说明
├── .env.example                        # 环境变量模板
├── rag开发指导.md                       # 完整开发指导文档
├── rag融合.md                          # 与 flavor-code 融合方案
│
├── docker/                             # Docker Compose 编排
│   ├── infra-stack.compose.yaml        # 基础中间件 (PG + Redis + RustFS)
│   ├── milvus-stack.compose.yaml       # Milvus 向量数据库
│   ├── es-stack.compose.yaml           # Elasticsearch (可选)
│   ├── rocketmq-stack.compose.yaml     # RocketMQ (可选)
│   └── lightrag-neo4j-stack.compose.yaml # LightRAG + Neo4j (可选)
│
├── database/
│   └── schema_pg.sql                   # PostgreSQL 建表脚本
│
├── ragent-server/                      # 服务端 (FastAPI)
│   ├── pyproject.toml
│   ├── run.py
│   └── app/
│       ├── main.py                     # FastAPI 入口
│       ├── config/settings.py          # 配置管理
│       ├── database/session.py         # 数据库连接
│       ├── models/__init__.py          # SQLAlchemy 模型
│       ├── auth/
│       │   ├── jwt.py                  # JWT Token 工具
│       │   └── dependencies.py         # 认证中间件
│       ├── api/
│       │   └── auth.py                 # 登录/注册 API
│       ├── rag/                        # RAG 核心引擎 (待实现)
│       ├── ingestion/                  # 文档入库流水线 (待实现)
│       └── llm/                        # LLM 客户端 (待实现)
│
└── ragent-frontend/                    # 前端 (React + Vite)
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── tailwind.config.cjs
    ├── index.html
    └── src/
        ├── main.tsx                    # 应用入口
        ├── App.tsx
        ├── router.tsx                  # 路由配置
        ├── types/index.ts              # TypeScript 类型
        ├── services/
        │   ├── api.ts                  # Axios 实例
        │   └── authService.ts          # 认证 API
        ├── stores/authStore.ts         # 认证状态
        ├── hooks/useStreamResponse.ts   # SSE 流式响应
        ├── pages/
        │   ├── LoginPage.tsx           # 登录/注册页
        │   ├── ChatPage.tsx            # 问答页 (骨架)
        │   └── NotFoundPage.tsx
        └── lib/utils.ts
```

## 快速开始

### 1. 启动基础设施

```bash
# 核心组件
docker compose -f docker/infra-stack.compose.yaml up -d

# 向量数据库
docker compose -f docker/milvus-stack.compose.yaml up -d
```

### 2. 初始化数据库

```bash
# 在 PostgreSQL 中执行建表脚本
docker exec -i ragent-postgres psql -U postgres -d ragent < database/schema_pg.sql
```

### 3. 启动服务端

```bash
cd ragent-server
pip install -e .
python run.py
# API 文档: http://localhost:9090/docs
```

### 4. 启动前端

```bash
cd ragent-frontend
npm install
npm run dev
# 访问: http://localhost:5173
```

## 开发路线图

参见 `rag开发指导.md` 第 9 节，分为 7 个阶段：

| 阶段 | 内容 | 状态 |
|------|------|------|
| 1 | 基础设施搭建 + 项目骨架 + 认证 | ✅ 已完成 |
| 2 | 核心 RAG 检索链路 (Embedding + Chunk + Milvus) | 🔲 待实现 |
| 3 | 问答对话 (LLM 流式 + SSE + 会话管理) | 🔲 待实现 |
| 4 | 增强检索 (ES + BM25 + Rerank) | 🔲 待实现 |
| 5 | 知识图谱 (Neo4j + LightRAG) | 🔲 待实现 |
| 6 | 高级特性 (Trace + 限流 + 模型路由) | 🔲 待实现 |
| 7 | 生产加固 (测试 + Docker 镜像 + 安全) | 🔲 待实现 |

## 与 flavor-code 融合

详见 `rag融合.md`。flavor-rag 可作为 flavor-code 的 RAG 后端服务，提供语义代码检索能力。
