"""mem0 long-term memory layer — extract, store, and retrieve user memory facts.

Uses the project's existing Milvus instance (new collection) + DeepSeek-V4-Flash
LLM for fact extraction + Qwen3-Embedding-8B for vectorization.
"""
