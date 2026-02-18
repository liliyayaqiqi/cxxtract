"""
Layer 1 - The Left Brain: Qdrant Vector Database Ingestion

This module provides functionality to ingest extracted C++ code entities
into a Qdrant vector database for semantic search and retrieval.
"""

from ingestion.qdrant_loader import (
    get_qdrant_client,
    init_collection,
    ingest_entities,
    ingest_from_jsonl,
    IngestionStats,
)
from ingestion.embedding import generate_mock_embedding

__all__ = [
    "get_qdrant_client",
    "init_collection",
    "ingest_entities",
    "ingest_from_jsonl",
    "IngestionStats",
    "generate_mock_embedding",
]
