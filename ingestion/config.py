"""
Configuration constants for Qdrant ingestion pipeline.

Defines connection parameters, collection defaults, and UUIDv5 namespace.
"""

import uuid
from qdrant_client import models

# Infrastructure configuration
DOCKER_COMPOSE_PATH: str = "infra_context/docker-compose.yml"
QDRANT_HOST: str = "127.0.0.1"
QDRANT_DEFAULT_PORT: int = 6333

# Collection configuration
DEFAULT_COLLECTION_NAME: str = "code_embeddings"
DEFAULT_VECTOR_DIMENSION: int = 1536  # Matches OpenAI text-embedding-3-small
DEFAULT_DISTANCE_METRIC = models.Distance.COSINE
DEFAULT_BATCH_SIZE: int = 100

# UUIDv5 namespace for deterministic ID generation
# Using RFC 4122 DNS namespace as a stable base
UUID_NAMESPACE: uuid.UUID = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# Connection retry configuration
CONNECTION_RETRIES: int = 3
CONNECTION_RETRY_DELAY: float = 2.0  # seconds
