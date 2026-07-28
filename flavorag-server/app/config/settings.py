from pathlib import Path
from pydantic_settings import BaseSettings

_ENV_FILE = str(Path(__file__).resolve().parent.parent.parent.parent / ".env")


class Settings(BaseSettings):
    # 服务器
    server_port: int = 9090

    # 数据库
    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/flavorag"

    # Redis
    redis_url: str = "redis://:123456@127.0.0.1:6379/0"

    # Milvus
    milvus_uri: str = "http://localhost:19530"

    # 对象存储 (RustFS / MinIO)
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "rustfsadmin"
    s3_secret_key: str = "rustfsadmin"
    s3_bucket: str = "flavorag-sources"
    source_storage_backend: str = "local"
    source_storage_prefix: str = "sources"

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # 模型供应商 API Key
    siliconflow_api_key: str = ""
    bailian_api_key: str = ""

    # Embedding
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    embedding_dim: int = 4096
    embedding_query_timeout_sec: float = 25.0
    embedding_query_max_attempts: int = 2

    # LLM
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus-latest"
    llm_max_output_tokens: int = 2048
    llm_generation_timeout_sec: float = 90.0
    llm_context_window_tokens: int = 8192
    llm_prompt_reserve_tokens: int = 800
    code_model: str = "deepseek-v3"
    doc_model: str = "qwen-plus-latest"

    # 复杂 PDF 图片理解（写入时调用，可选）
    vlm_enabled: bool = False
    vlm_base_url: str = ""
    vlm_api_key: str = ""
    vlm_model: str = "qwen-vl-plus"
    vlm_max_output_tokens: int = 800
    vlm_max_concurrency: int = 2

    # 复杂 PDF 入库
    pdf_table_max_rows: int = 20
    pdf_image_min_area_ratio: float = 0.001
    pdf_asset_storage_required: bool = True
    pdf_ocr_enabled: bool = True
    pdf_ocr_min_native_chars: int = 20
    pdf_ocr_dpi: int = 180

    # 深度思考 / 推理模型
    reasoning_model: str = ""
    reasoning_base_url: str = ""
    reasoning_api_key: str = ""

    # Reranker
    reranker_base_url: str = "https://api.siliconflow.cn/v1"
    reranker_model: str = "Qwen/Qwen3-Reranker-8B"
    reranker_enabled: bool = True
    reranker_timeout_sec: float = 15.0

    # Retrieval governance
    retrieval_per_channel_top_k: int = 12
    retrieval_max_candidates: int = 40
    retrieval_final_top_k: int = 5
    retrieval_channel_timeout_ms: int = 30000
    retrieval_total_timeout_ms: int = 35000
    retrieval_context_max_chars: int = 12000
    retrieval_context_max_tokens: int = 3000
    retrieval_min_relevance_score: float = 0.0
    retrieval_rrf_min_score: float = 0.012
    retrieval_vector_min_score: float = 0.35
    retrieval_reranker_min_score: float = 0.0
    query_decomposition_enabled: bool = True
    query_decomposition_max_queries: int = 3
    retrieval_channel_weights: str = "vector:1.0,keyword:1.0,graph:0.65"
    circuit_breaker_failures: int = 3
    circuit_breaker_recovery_sec: int = 30

    # Elasticsearch（可选）
    es_uris: str = "http://127.0.0.1:9200"
    es_enabled: bool = True
    es_required: bool = False
    # 中文分词器（需安装 IK 插件；未安装时自动降级 standard）
    es_analyzer: str = "ik_max_word"
    es_search_analyzer: str = "ik_smart"

    # LightRAG（可选）
    lightrag_base_url: str = "http://127.0.0.1:9621"
    lightrag_api_key: str = ""
    graph_enabled: bool = False
    graph_required: bool = False
    # Evidence-grounded semantic graph extraction. The provider settings fall
    # back to the regular LLM configuration when left blank.
    graph_semantic_enabled: bool = True
    graph_semantic_model: str = "qwen-plus-latest"
    graph_semantic_base_url: str = ""
    graph_semantic_api_key: str = ""
    graph_semantic_temperature: float = 0.0
    graph_semantic_max_tokens: int = 2048
    graph_semantic_max_input_chars: int = 4000
    graph_semantic_batch_chunks: int = 6
    graph_semantic_max_entities_per_batch: int = 12
    graph_semantic_max_relationships_per_batch: int = 16
    graph_semantic_timeout_sec: float = 45.0
    graph_semantic_min_confidence: float = 0.70
    graph_semantic_max_entity_name_chars: int = 120
    graph_semantic_max_description_chars: int = 360
    graph_semantic_min_evidence_chars: int = 8
    graph_semantic_max_evidence_chars: int = 600
    graph_semantic_require_endpoints_in_evidence: bool = True
    graph_semantic_reject_negative_stores: bool = True
    graph_semantic_validate_part_of_direction: bool = True
    graph_semantic_provider_fallback_enabled: bool = True
    graph_semantic_backfill_concurrency: int = 2
    graph_semantic_prompt_version: str = "v1"

    # Neo4j（可选）
    neo4j_uri: str = "neo4j://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password123"

    # RocketMQ（可选）
    rocketmq_name_server: str = "127.0.0.1:9876"

    # 查询重写
    rewrite_enabled: bool = True  # 是否启用 LLM 查询重写

    # 意图识别
    intent_llm_enabled: bool = True  # 是否启用 LLM 意图分类
    intent_min_score: float = 0.3
    intent_max_matches: int = 5
    intent_guidance_min_score: float = 0.55
    intent_guidance_score_gap: float = 0.08
    query_understanding_timeout_sec: float = 20.0

    # TTFT 优化
    ttft_parallel_rewrite_intent: bool = True  # 并行执行 rewrite + intent
    ttft_early_feedback: bool = True  # 立即发送 progress 事件给前端
    ttft_speculative_search: bool = True  # rewrite 期间用原始 query 预搜索

    # HyDE（Hypothetical Document Embeddings）
    hyde_enabled: bool = False               # 全局默认开关（前端可逐请求覆盖）
    hyde_model: str = "qwen-turbo-latest"    # 轻量模型，用于生成假设文档
    hyde_base_url: str = ""                  # 为空时复用 llm_base_url
    hyde_api_key: str = ""                   # 为空时复用 bailian_api_key / siliconflow_api_key
    hyde_max_tokens: int = 512               # 假设文档最大 token 数
    hyde_temperature: float = 0.7            # 生成温度（略高以增加多样性）
    hyde_channel_weight: float = 0.8         # HyDE 通道在 RRF 融合中的权重
    hyde_timeout_sec: float = 15.0            # 单次 HyDE 生成超时

    # 可观测性
    metrics_enabled: bool = True
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:14318"
    otel_service_name: str = "flavorag-server"
    prometheus_ui_url: str = "http://localhost:19090"
    grafana_ui_url: str = "http://localhost:13000"
    jaeger_ui_url: str = "http://localhost:16687"
    trace_store_content: bool = False
    trace_retention_days: int = 30
    index_retired_retention_days: int = 7

    # 异步摄取（Outbox 任务表 + Worker）
    ingestion_async_enabled: bool = True
    ingestion_worker_concurrency: int = 2
    ingestion_worker_poll_interval_sec: int = 3
    ingestion_job_max_attempts: int = 3
    ingestion_job_claim_timeout_sec: int = 900

    # 限流
    rate_limit_enabled: bool = False
    rate_limit_user_qpm: int = 60
    rate_limit_ip_qpm: int = 600

    # URL ingestion safety
    url_ingestion_max_bytes: int = 52428800
    url_ingestion_timeout_sec: int = 120
    url_ingestion_max_redirects: int = 5
    url_allow_private_networks: bool = False
    upload_max_bytes: int = 52428800
    upload_batch_max_files: int = 20
    upload_max_pdf_pages: int = 500
    archive_max_uncompressed_bytes: int = 209715200
    archive_max_entries: int = 10000
    archive_max_compression_ratio: float = 200.0
    upload_max_image_pixels: int = 100_000_000
    pdf_ocr_max_concurrency: int = 4

    # Conversation and controlled agent
    conversation_summary_trigger_messages: int = 16
    conversation_summary_keep_recent_messages: int = 8
    conversation_context_max_tokens: int = 2000
    chat_max_input_tokens: int = 3000
    recommended_questions_enabled: bool = True
    recommended_questions_count: int = 3
    agentic_rag_enabled: bool = False
    agent_max_steps: int = 4
    agent_tool_timeout_sec: int = 10
    # Agentic 最终生成：前三次使用高质量模型，失败后降级两次。
    agentic_primary_model: str = ""  # 为空时复用 reasoning_model
    agentic_fallback_model: str = ""  # 为空时复用 hyde_model / mem0_model
    agentic_model_base_url: str = ""
    agentic_model_api_key: str = ""
    agentic_primary_attempts: int = 3
    agentic_fallback_attempts: int = 2
    agentic_retry_base_delay_sec: float = 1.0
    agentic_retry_max_delay_sec: float = 8.0
    agentic_replay_chunk_chars: int = 2
    agentic_replay_interval_ms: int = 18
    sql_tool_enabled: bool = False
    sql_tool_allowed_relations: str = ""
    mcp_tool_enabled: bool = False
    mcp_allowed_tools: str = ""
    mcp_tools_json: str = "{}"

    # mem0 长期记忆 + 用户画像
    mem0_enabled: bool = True                          # 是否启用 mem0 记忆层
    mem0_collection_name: str = "user_memories"        # Milvus collection 名称
    mem0_model: str = "deepseek-v4-flash"              # 记忆提取 LLM（复用 HyDE 配置）
    mem0_base_url: str = "https://api.deepseek.com/v1"  # LLM base URL
    mem0_api_key: str = ""                             # 为空时复用 hyde_api_key
    mem0_max_tokens: int = 1024                        # 提取 prompt 最大 token
    mem0_timeout_sec: float = 20.0                     # 提取超时
    mem0_search_top_k: int = 5                         # 检索时拉取的记忆条数

    # 用户画像更新策略：incremental（每次对话后增量）| daily（每日定时全量）
    mem0_min_relevance_score: float = 0.35
    profile_update_mode: str = "incremental"
    profile_daily_cron: str = "0 2 * * *"              # 每日凌晨 2 点执行
    profile_llm_model: str = "deepseek-v4-flash"        # 画像聚合 LLM
    profile_llm_base_url: str = "https://api.deepseek.com/v1"
    profile_llm_api_key: str = ""                     # 为空时复用 mem0_api_key
    profile_min_queries_for_build: int = 5             # 最少提问数才构建画像

    # flavor-code 集成
    flavor_code_integration_enabled: bool = False
    flavor_code_api_tokens: str = ""

    model_config = {"env_file": _ENV_FILE, "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
