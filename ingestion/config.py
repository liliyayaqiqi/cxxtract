"""
Configuration constants for Qdrant ingestion and OpenRouter embedding pipeline.

Defines connection parameters, collection defaults, UUIDv5 namespace,
and OpenRouter API configuration. Environment variables are loaded from
a .env file at module import time via python-dotenv.
"""

import os
import uuid

from dotenv import load_dotenv
from qdrant_client import models

# ---------------------------------------------------------------------------
# Load .env file (idempotent; does nothing if already loaded or missing)
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# Qdrant infrastructure configuration
# ---------------------------------------------------------------------------
DOCKER_COMPOSE_PATH: str = "infra_context/docker-compose.yml"
QDRANT_HOST: str = "127.0.0.1"
QDRANT_DEFAULT_PORT: int = 6333

# ---------------------------------------------------------------------------
# Collection configuration
# ---------------------------------------------------------------------------
DEFAULT_COLLECTION_NAME: str = "code_embeddings"
DEFAULT_VECTOR_DIMENSION: int = 1536  # Matches OpenAI text-embedding-3-small
DEFAULT_DISTANCE_METRIC = models.Distance.COSINE
DEFAULT_BATCH_SIZE: int = 100

# ---------------------------------------------------------------------------
# UUIDv5 namespace for deterministic ID generation
# Using RFC 4122 DNS namespace as a stable base
# ---------------------------------------------------------------------------
UUID_NAMESPACE: uuid.UUID = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# ---------------------------------------------------------------------------
# Connection retry configuration
# ---------------------------------------------------------------------------
CONNECTION_RETRIES: int = 3
CONNECTION_RETRY_DELAY: float = 2.0  # seconds

# ---------------------------------------------------------------------------
# OpenRouter / Embedding API configuration
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
EMBEDDING_MODEL: str = "openai/text-embedding-3-small"

# Toggle: set USE_MOCK_EMBEDDING=true in .env to skip real API calls
USE_MOCK_EMBEDDING: bool = os.getenv("USE_MOCK_EMBEDDING", "false").lower() == "true"

# Conservative token limit for text-embedding-3-small (8191 tokens max)
# Using 1 token ≈ 4 characters as the conservative truncation rule
MAX_EMBED_TOKENS: int = 8191
MAX_EMBED_CHARS: int = MAX_EMBED_TOKENS * 4  # 32764 characters

# Retry configuration for embedding API calls (used by tenacity)
EMBEDDING_MAX_RETRIES: int = 5
EMBEDDING_RETRY_MIN_WAIT: int = 2   # seconds
EMBEDDING_RETRY_MAX_WAIT: int = 20  # seconds
