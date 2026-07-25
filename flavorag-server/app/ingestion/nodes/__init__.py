"""Ingestion pipeline node types.

Each node is a callable that receives an IngestionContext and returns
a NodeResult. Nodes are composed into a DAG by the IngestionEngine.

Node types:
  - fetcher:   Download document from source (file/URL/feishu)
  - parser:    Parse document into plain text
  - chunker:   Split text into chunks
  - enricher:  Add metadata to chunks (keywords, summary)
  - enhancer:  Rewrite chunks for better retrieval
  - indexer:   Embed + store in Milvus + PG
"""

from app.ingestion.nodes.fetcher import FetcherNode
from app.ingestion.nodes.parser_node import ParserNode
from app.ingestion.nodes.chunker_node import ChunkerNode
from app.ingestion.nodes.enricher import EnricherNode
from app.ingestion.nodes.enhancer import EnhancerNode
from app.ingestion.nodes.indexer import IndexerNode

__all__ = [
    "FetcherNode",
    "ParserNode",
    "ChunkerNode",
    "EnricherNode",
    "EnhancerNode",
    "IndexerNode",
]
