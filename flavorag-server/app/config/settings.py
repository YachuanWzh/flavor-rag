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

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # 模型供应商 API Key
    siliconflow_api_key: str = ""
    bailian_api_key: str = ""
    aihubmix_api_key: str = ""

    # Embedding
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    embedding_dim: int = 4096
    embedding_query_timeout_sec: float = 25.0
    embedding_query_max_attempts: int = 1

    # LLM
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus-latest"
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
    retrieval_min_relevance_score: float = 0.01
    query_decomposition_enabled: bool = True
    query_decomposition_max_queries: int = 3
    retrieval_channel_weights: str = "vector:1.0,keyword:1.0,graph:0.65"
    circuit_breaker_failures: int = 3
    circuit_breaker_recovery_sec: int = 30

    # Elasticsearch（可选）
    es_uris: str = "http://127.0.0.1:9200"
    es_enabled: bool = True

    # LightRAG（可选）
    lightrag_base_url: str = "http://127.0.0.1:9621"
    lightrag_api_key: str = ""
    graph_enabled: bool = False

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

    # 可观测性
    metrics_enabled: bool = True
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_service_name: str = "flavorag-server"

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

    # Conversation and controlled agent
    conversation_summary_trigger_messages: int = 16
    conversation_summary_keep_recent_messages: int = 8
    recommended_questions_enabled: bool = True
    recommended_questions_count: int = 3
    agentic_rag_enabled: bool = False
    agent_max_steps: int = 4
    agent_tool_timeout_sec: int = 10
    sql_tool_enabled: bool = False
    sql_tool_allowed_relations: str = ""
    mcp_tool_enabled: bool = False
    mcp_allowed_tools: str = ""
    mcp_tools_json: str = "{}"

    # flavor-code 集成
    flavor_code_integration_enabled: bool = False
    flavor_code_api_tokens: str = ""

    model_config = {"env_file": _ENV_FILE, "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
