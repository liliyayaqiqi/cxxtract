"""
Qdrant vector database ingestion pipeline.

Provides functions to connect to Qdrant, initialize collections,
and upsert code entity embeddings with deterministic IDs.

Embedding generation is performed in **batches** — one API call per
chunk of entities — by delegating to ``ingestion.embedding.get_embeddings``.
"""

import json
import logging
import time
import uuid
import yaml
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Callable

from qdrant_client import QdrantClient, models

from ingestion.config import (
    DOCKER_COMPOSE_PATH,
    QDRANT_HOST,
    QDRANT_DEFAULT_PORT,
    DEFAULT_COLLECTION_NAME,
    DEFAULT_VECTOR_DIMENSION,
    DEFAULT_DISTANCE_METRIC,
    DEFAULT_BATCH_SIZE,
    UUID_NAMESPACE,
    CONNECTION_RETRIES,
    CONNECTION_RETRY_DELAY,
)
from ingestion.embedding import get_embeddings

logger = logging.getLogger(__name__)


@dataclass
class IngestionStats:
    """Statistics for an ingestion operation."""

    points_uploaded: int = 0
    batches_sent: int = 0
    errors: int = 0

    def __str__(self) -> str:
        """String representation of stats."""
        return (
            f"IngestionStats(points={self.points_uploaded}, "
            f"batches={self.batches_sent}, errors={self.errors})"
        )


def get_qdrant_client() -> QdrantClient:
    """Connect to Qdrant instance using configuration from docker-compose.yml.

    Parses the docker-compose configuration to extract the Qdrant port,
    then establishes a connection with retry logic.

    Returns:
        Connected QdrantClient instance.

    Raises:
        ConnectionError: If unable to connect after retries.
        FileNotFoundError: If docker-compose.yml not found.

    Example:
        >>> client = get_qdrant_client()
        >>> collections = client.get_collections()
    """
    # Parse docker-compose.yml for Qdrant port
    port = QDRANT_DEFAULT_PORT

    try:
        with open(DOCKER_COMPOSE_PATH, "r") as f:
            config = yaml.safe_load(f)

        qdrant_config = config.get("services", {}).get("qdrant", {})
        ports = qdrant_config.get("ports", [])

        # Find the port mapping for 6333 (Qdrant HTTP API)
        for port_mapping in ports:
            port_str = str(port_mapping)
            if ":6333" in port_str:
                # Extract host port from "6333:6333" format
                port = int(port_str.split(":")[0])
                break

        logger.debug(f"Extracted Qdrant port from docker-compose: {port}")

    except FileNotFoundError:
        logger.warning(
            f"Could not find {DOCKER_COMPOSE_PATH}, using default port {QDRANT_DEFAULT_PORT}"
        )
    except Exception as e:
        logger.warning(f"Error parsing docker-compose.yml: {e}, using default port")

    # Attempt connection with retries
    for attempt in range(CONNECTION_RETRIES):
        try:
            client = QdrantClient(host=QDRANT_HOST, port=port)

            # Verify connectivity with a lightweight operation
            client.get_collections()

            logger.info(f"Connected to Qdrant at {QDRANT_HOST}:{port}")
            return client

        except Exception as e:
            logger.warning(
                f"Connection attempt {attempt + 1}/{CONNECTION_RETRIES} failed: {e}"
            )
            if attempt < CONNECTION_RETRIES - 1:
                time.sleep(CONNECTION_RETRY_DELAY)
            else:
                raise ConnectionError(
                    f"Failed to connect to Qdrant at {QDRANT_HOST}:{port} "
                    f"after {CONNECTION_RETRIES} attempts"
                ) from e

    # Should never reach here, but satisfy type checker
    raise ConnectionError("Unexpected error in connection logic")


def init_collection(
    client: QdrantClient,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    vector_dimension: int = DEFAULT_VECTOR_DIMENSION,
    recreate: bool = False,
) -> None:
    """Initialize or recreate a Qdrant collection for code embeddings.

    Args:
        client: Connected QdrantClient instance.
        collection_name: Name of the collection to create/verify.
        vector_dimension: Dimension of embedding vectors.
        recreate: If True, delete existing collection and recreate.
                 If False, create only if it doesn't exist (idempotent).

    Raises:
        Exception: If collection creation fails.

    Example:
        >>> client = get_qdrant_client()
        >>> init_collection(client, "my_code", 1536, recreate=True)
    """
    try:
        # Check if collection exists
        exists = client.collection_exists(collection_name)

        if exists and recreate:
            logger.info(f"Deleting existing collection '{collection_name}'")
            client.delete_collection(collection_name)
            exists = False

        if not exists:
            logger.info(
                f"Creating collection '{collection_name}' "
                f"(dimension={vector_dimension}, metric={DEFAULT_DISTANCE_METRIC})"
            )

            client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=vector_dimension,
                    distance=DEFAULT_DISTANCE_METRIC,
                ),
            )

            logger.info(
                f"Collection '{collection_name}' created, now creating payload indices..."
            )

            # CRITICAL: Create payload indices for hybrid search performance.
            # Without these, Qdrant performs full-scan on filtered queries.
            indexed_fields = [
                "global_uri",
                "repo_name",
                "file_path",
                "entity_type",
                "entity_name",
            ]

            for field_name in indexed_fields:
                client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
                logger.debug(f"Created KEYWORD index on field '{field_name}'")

            logger.info(
                f"Collection '{collection_name}' created with "
                f"{len(indexed_fields)} payload indices"
            )
        else:
            logger.info(
                f"Collection '{collection_name}' already exists, skipping creation"
            )

    except Exception as e:
        logger.error(f"Failed to initialize collection '{collection_name}': {e}")
        raise


def generate_point_id(global_uri: str) -> str:
    """Generate a deterministic UUID for a code entity.

    Uses UUIDv5 with a fixed namespace to ensure the same global_uri
    always produces the same UUID. This enables idempotent upserts —
    re-ingesting the same entity will overwrite the existing point,
    not create duplicates.

    Args:
        global_uri: The unique identifier for the code entity.

    Returns:
        String representation of UUIDv5.

    Example:
        >>> id1 = generate_point_id("repo::file.cpp::Class::Foo")
        >>> id2 = generate_point_id("repo::file.cpp::Class::Foo")
        >>> id1 == id2  # Same URI always produces same UUID
        True
    """
    return str(uuid.uuid5(UUID_NAMESPACE, global_uri))


def _build_embed_text(entity_dict: Dict[str, Any]) -> str:
    """Build the text string that will be sent to the embedding model.

    Concatenates ``docstring`` (if present) and ``code_text`` so the
    embedding captures both natural-language intent and syntactic
    structure.

    Args:
        entity_dict: Dictionary representation of an ExtractedEntity.

    Returns:
        Concatenated text for embedding.
    """
    docstring = entity_dict.get("docstring")
    code_text = entity_dict["code_text"]

    if docstring:
        return f"{docstring}\n{code_text}"
    return code_text


def build_point(
    entity_dict: Dict[str, Any],
    vector: List[float],
) -> models.PointStruct:
    """Build a Qdrant point from an entity dictionary and a pre-computed vector.

    This function is intentionally **embedding-agnostic** — it receives the
    vector that was already generated externally (by the batch embedding
    call in ``ingest_entities``).

    Args:
        entity_dict: Dictionary representation of ExtractedEntity.
        vector: Pre-computed embedding vector for this entity.

    Returns:
        PointStruct ready for upsert to Qdrant.

    Raises:
        KeyError: If required fields missing from entity_dict.

    Example:
        >>> entity = {"global_uri": "...", "code_text": "...", ...}
        >>> vec = [0.1, 0.2, ...]
        >>> point = build_point(entity, vec)
    """
    global_uri = entity_dict["global_uri"]

    # Generate deterministic point ID
    point_id = generate_point_id(global_uri)

    # Build payload — store ALL entity metadata
    payload = {
        "global_uri": entity_dict["global_uri"],
        "repo_name": entity_dict["repo_name"],
        "file_path": entity_dict["file_path"],
        "entity_type": entity_dict["entity_type"],
        "entity_name": entity_dict["entity_name"],
        "docstring": entity_dict.get("docstring"),  # Can be None
        "code_text": entity_dict["code_text"],
        "start_line": entity_dict["start_line"],
        "end_line": entity_dict["end_line"],
        "is_templated": entity_dict["is_templated"],
    }

    return models.PointStruct(
        id=point_id,
        vector=vector,
        payload=payload,
    )


def ingest_entities(
    entities: List[Dict[str, Any]],
    client: QdrantClient,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    embed_fn: Optional[Callable[[List[str], int], List[List[float]]]] = None,
    dimension: int = DEFAULT_VECTOR_DIMENSION,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> IngestionStats:
    """Ingest code entities into Qdrant with batched embedding and upload.

    For each batch of entities:

    1. Extract the embedding text (``docstring + code_text``) for every
       entity in the batch.
    2. Call ``embed_fn(texts, dimension)`` **exactly once** per batch to
       get all vectors in a single API round-trip.
    3. Pair each entity with its vector via ``zip()`` and build
       ``PointStruct`` objects.
    4. Upsert the batch to Qdrant.

    Args:
        entities: List of entity dictionaries (from ``ExtractedEntity.to_dict()``).
        client: Connected QdrantClient instance.
        collection_name: Target collection name.
        embed_fn: Batch embedding function ``(List[str], int) -> List[List[float]]``.
                 If None, uses ``get_embeddings`` (the config-aware router).
        dimension: Vector dimension for embeddings.
        batch_size: Number of points to upload per batch.

    Returns:
        IngestionStats with upload metrics.

    Example:
        >>> entities = [entity.to_dict() for entity in extracted_entities]
        >>> client = get_qdrant_client()
        >>> stats = ingest_entities(entities, client)
        >>> print(f"Uploaded {stats.points_uploaded} points")
    """
    if embed_fn is None:
        embed_fn = get_embeddings

    stats = IngestionStats()

    # Process in batches
    for i in range(0, len(entities), batch_size):
        batch = entities[i : i + batch_size]

        # ------------------------------------------------------------------
        # Step 1: Extract embedding texts for the entire batch
        # ------------------------------------------------------------------
        embed_texts: List[str] = []
        valid_entities: List[Dict[str, Any]] = []

        for entity in batch:
            try:
                text = _build_embed_text(entity)
                embed_texts.append(text)
                valid_entities.append(entity)
            except KeyError as e:
                logger.error(
                    "Missing required field %s in entity %s — skipping",
                    e,
                    entity.get("global_uri", "unknown"),
                )
                stats.errors += 1

        if not embed_texts:
            continue

        # ------------------------------------------------------------------
        # Step 2: Generate embeddings — ONE call per batch
        # ------------------------------------------------------------------
        try:
            vectors = embed_fn(embed_texts, dimension)
        except Exception as e:
            logger.error(
                "Batch embedding failed for %d texts: %s", len(embed_texts), e
            )
            stats.errors += len(valid_entities)
            continue

        # Sanity check: API must return exactly one vector per text
        if len(vectors) != len(valid_entities):
            logger.error(
                "Embedding count mismatch: expected %d, got %d — skipping batch",
                len(valid_entities),
                len(vectors),
            )
            stats.errors += len(valid_entities)
            continue

        # ------------------------------------------------------------------
        # Step 3: Build PointStruct objects from (entity, vector) pairs
        # ------------------------------------------------------------------
        batch_points: List[models.PointStruct] = []

        for entity, vector in zip(valid_entities, vectors):
            # Verify vector dimension
            if len(vector) != dimension:
                logger.error(
                    "Dimension mismatch for %s: expected %d, got %d — skipping",
                    entity.get("global_uri", "unknown"),
                    dimension,
                    len(vector),
                )
                stats.errors += 1
                continue

            try:
                point = build_point(entity, vector)
                batch_points.append(point)
            except Exception as e:
                logger.error(
                    "Failed to build point for %s: %s",
                    entity.get("global_uri", "unknown"),
                    e,
                )
                stats.errors += 1

        # ------------------------------------------------------------------
        # Step 4: Upsert batch to Qdrant
        # ------------------------------------------------------------------
        if batch_points:
            try:
                client.upsert(
                    collection_name=collection_name,
                    points=batch_points,
                )

                stats.points_uploaded += len(batch_points)
                stats.batches_sent += 1

                logger.info(
                    "Uploaded batch %d: %d points (total: %d)",
                    stats.batches_sent,
                    len(batch_points),
                    stats.points_uploaded,
                )

            except Exception as e:
                logger.error("Failed to upsert batch: %s", e)
                stats.errors += len(batch_points)

    logger.info("Ingestion complete: %s", stats)
    return stats


def ingest_from_jsonl(
    file_path: str,
    client: QdrantClient,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    embed_fn: Optional[Callable[[List[str], int], List[List[float]]]] = None,
    dimension: int = DEFAULT_VECTOR_DIMENSION,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> IngestionStats:
    """Ingest code entities from a JSONL file into Qdrant.

    Reads a JSONL file (one JSON object per line) where each line represents
    an entity dictionary, then delegates to ``ingest_entities()``.

    Args:
        file_path: Path to JSONL file.
        client: Connected QdrantClient instance.
        collection_name: Target collection name.
        embed_fn: Batch embedding function. If None, uses ``get_embeddings``.
        dimension: Vector dimension for embeddings.
        batch_size: Number of points to upload per batch.

    Returns:
        IngestionStats with upload metrics.

    Raises:
        FileNotFoundError: If JSONL file not found.
        json.JSONDecodeError: If JSONL is malformed.

    Example:
        >>> client = get_qdrant_client()
        >>> stats = ingest_from_jsonl("entities.jsonl", client)
    """
    logger.info(f"Reading entities from {file_path}")

    entities: List[Dict[str, Any]] = []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue  # Skip empty lines

                try:
                    entity = json.loads(line)
                    entities.append(entity)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON on line {line_num}: {e}")
                    raise

        logger.info(f"Loaded {len(entities)} entities from {file_path}")

    except FileNotFoundError:
        logger.error(f"JSONL file not found: {file_path}")
        raise

    # Delegate to in-memory ingestion
    return ingest_entities(
        entities=entities,
        client=client,
        collection_name=collection_name,
        embed_fn=embed_fn,
        dimension=dimension,
        batch_size=batch_size,
    )


def main() -> None:
    """Manual end-to-end verification of the ingestion pipeline."""

    # Configure logging for manual run
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("=" * 80)
    logger.info(" Qdrant Ingestion Pipeline - Manual Verification")
    logger.info("=" * 80)

    try:
        # Import extraction module
        from extraction.extractor import extract_file

        # Parse test_torture.h
        logger.info("\n1. Extracting entities from test_torture.h...")
        entities_obj = extract_file(
            "extraction/tests/fixtures/test_torture.h", "rtc_engine"
        )

        # Convert to dict list
        entities = [asdict(e) for e in entities_obj]
        logger.info(f"   Extracted {len(entities)} entities")

        # Connect to Qdrant
        logger.info("\n2. Connecting to Qdrant...")
        client = get_qdrant_client()

        # Initialize collection (recreate to start fresh)
        logger.info("\n3. Initializing collection...")
        init_collection(client, collection_name="test_code", recreate=True)

        # Ingest entities
        logger.info("\n4. Ingesting entities...")
        stats = ingest_entities(entities, client, collection_name="test_code")

        # Verify by scrolling collection
        logger.info("\n5. Verifying upload...")
        scroll_result = client.scroll(collection_name="test_code", limit=10)

        points = scroll_result[0]
        logger.info(f"   Retrieved {len(points)} points from Qdrant:")
        for point in points:
            payload = point.payload
            if payload is not None:
                uri = payload.get("global_uri", "unknown")
                entity_type = payload.get("entity_type", "unknown")
            else:
                uri = "unknown"
                entity_type = "unknown"
            logger.info(f"     - {entity_type}: {uri}")

        logger.info("\n" + "=" * 80)
        logger.info(" Manual verification complete!")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"\nManual verification failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
