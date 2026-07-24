from pathlib import Path
from pydantic_settings import BaseSettings

_ENV_FILE = str(Path(__file__).resolve().parent.parent.parent / ".env")


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

    # LLM
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus-latest"
    code_model: str = "deepseek-v3"
    doc_model: str = "qwen-plus-latest"

    # Reranker
    reranker_base_url: str = "https://api.siliconflow.cn/v1"
    reranker_model: str = "Qwen/Qwen3-Reranker"

    # Elasticsearch（可选）
    es_uris: str = "http://127.0.0.1:9200"
    es_enabled: bool = False

    # LightRAG（可选）
    lightrag_base_url: str = "http://127.0.0.1:9621"
    graph_enabled: bool = False

    # Neo4j（可选）
    neo4j_uri: str = "neo4j://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password123"

    # RocketMQ（可选）
    rocketmq_name_server: str = "127.0.0.1:9876"

    # 限流
    rate_limit_enabled: bool = False
    rate_limit_user_qpm: int = 60
    rate_limit_ip_qpm: int = 600

    # flavor-code 集成
    flavor_code_integration_enabled: bool = False
    flavor_code_api_tokens: str = ""

    model_config = {"env_file": _ENV_FILE, "env_file_encoding": "utf-8"}


settings = Settings()
