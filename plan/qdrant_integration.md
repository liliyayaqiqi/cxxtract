#cQdrant Ingestion Pipeline — Implementation Plan
1. File Structure
ingestion/
├── __init__.py
├── config.py              # Qdrant connection constants, collection defaults
├── qdrant_loader.py       # Core module: connect, init collection, upsert
├── embedding.py           # Mock embedding generator (swappable later)
└── tests/
    ├── __init__.py
    └── test_qdrant_loader.py
2. Dependencies
No new packages required. Already in requirements.txt:
| Package | Version | Purpose |
|---|---|---|
| qdrant-client | >=1.7.0 (installed: 1.16.2) | Qdrant Python SDK |
| pyyaml | >=6.0 | Parse docker-compose.yml for connection config |
Standard library only:
- uuid (for uuid5 generation)
- random (for mock embeddings)
- json (for JSONL parsing)
- logging, typing
3. Module Design
3.1. ingestion/config.py — Constants
| Constant | Type | Default | Description |
|---|---|---|---|
| DOCKER_COMPOSE_PATH | str | "infra_context/docker-compose.yml" | Path to docker-compose config |
| QDRANT_HOST | str | "127.0.0.1" | Host (mirrors test_connections.py logic) |
| DEFAULT_COLLECTION_NAME | str | "code_embeddings" | Default collection name |
| DEFAULT_VECTOR_DIMENSION | int | 1536 | Matches OpenAI text-embedding-3-small |
| DEFAULT_DISTANCE_METRIC | str | "Cosine" | Qdrant distance function |
| DEFAULT_BATCH_SIZE | int | 100 | Points per upsert batch |
| UUID_NAMESPACE | uuid.UUID | uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8") | RFC 4122 DNS namespace for UUIDv5 |
3.2. ingestion/embedding.py — Mock Embedding Generator
One function:
generate_mock_embedding(text: str, dimension: int = 1536) -> List[float]
Algorithm:
1. Seed random.Random with a hash of the input text (hash(text)). This makes it deterministic — the same input always produces the same mock vector. Critical for idempotent re-runs.
2. Generate a list of dimension random floats in range [-1.0, 1.0].
3. Return the list.
This function will be the only thing swapped out when integrating a real embedding API later.
3.3. ingestion/qdrant_loader.py — Core Module
Function 1: get_qdrant_client() -> QdrantClient
Logic:
1. Parse infra_context/docker-compose.yml using yaml.safe_load(). Reuse the load_config / parse_port pattern from test_connections.py.
2. Extract the Qdrant HTTP port from the qdrant service's ports list (find the mapping ending in :6333).
3. Instantiate QdrantClient(host="127.0.0.1", port=<extracted_port>).
4. Call client.get_collections() as a connectivity health check.
5. Return the client. Raise ConnectionError on failure after retries.
Function 2: init_collection(client, name, vector_dim, recreate) -> None
Signature:
def init_collection(
    client: QdrantClient,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    vector_dimension: int = DEFAULT_VECTOR_DIMENSION,
    recreate: bool = False
) -> None
Logic:
1. If recreate=True: call client.delete_collection(collection_name) (ignore if not exists), then create.
2. If recreate=False: check if collection already exists via client.collection_exists(collection_name). If yes, log and return early. If no, create.
3. Create via:
      client.create_collection(
       collection_name=collection_name,
       vectors_config=models.VectorParams(
           size=vector_dimension,
           distance=models.Distance.COSINE
       )
   )
   4. Log success with collection name and dimension.
Function 3: generate_point_id(global_uri: str) -> str
Idempotency Algorithm (UUIDv5):
1. Import uuid.
2. Use a fixed namespace UUID (the RFC 4122 DNS namespace, or a custom project-specific one defined in config).
3. Generate: point_id = str(uuid.uuid5(UUID_NAMESPACE, global_uri)).
4. Return the string UUID.
Why UUIDv5: It is a deterministic hash (SHA-1) of (namespace, name). The same global_uri always produces the same UUID. Qdrant accepts string UUIDs as point IDs. Re-upserting the same ID overwrites — no duplicates.
Function 4: build_point(entity_dict, embed_fn, dimension) -> PointStruct
Signature:
def build_point(
    entity_dict: Dict[str, Any],
    embed_fn: Callable[[str], List[float]],
    dimension: int = DEFAULT_VECTOR_DIMENSION
) -> models.PointStruct
Logic:
1. Extract global_uri from entity_dict.
2. Generate point_id = generate_point_id(global_uri).
3. Build the text to embed: concatenate docstring (if not None) + "\n" + code_text. This gives the embedding model the richest context.
4. Generate vector: vector = embed_fn(embed_text, dimension).
5. Build payload — all fields from entity_dict go into the payload:
      payload = {
       "global_uri": ...,
       "repo_name": ...,
       "file_path": ...,
       "entity_type": ...,
       "entity_name": ...,
       "docstring": ...,
       "code_text": ...,
       "start_line": ...,
       "end_line": ...,
       "is_templated": ...
   }
   6. Return models.PointStruct(id=point_id, vector=vector, payload=payload).
Function 5: ingest_entities(entities, ...) -> IngestionStats
Signature:
def ingest_entities(
    entities: List[Dict[str, Any]],
    client: QdrantClient,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    embed_fn: Optional[Callable] = None,
    dimension: int = DEFAULT_VECTOR_DIMENSION,
    batch_size: int = DEFAULT_BATCH_SIZE
) -> IngestionStats
Logic:
1. If embed_fn is None, default to generate_mock_embedding.
2. Initialize an IngestionStats counter (points_uploaded, batches_sent, errors).
3. Iterate over entities in chunks of batch_size:
   - For each entity dict in the batch, call build_point(entity, embed_fn, dimension).
   - Wrap each build_point in try/except; on error, log and increment stats.errors, skip the point.
   - Collect valid PointStruct objects into a list.
   - Call client.upsert(collection_name=collection_name, points=batch_points).
   - Increment stats.
4. Log final stats.
5. Return stats.
Function 6: ingest_from_jsonl(file_path, ...) -> IngestionStats
Signature:
def ingest_from_jsonl(
    file_path: str,
    client: QdrantClient,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    embed_fn: Optional[Callable] = None,
    dimension: int = DEFAULT_VECTOR_DIMENSION,
    batch_size: int = DEFAULT_BATCH_SIZE
) -> IngestionStats
Logic:
1. Open file_path, read line by line.
2. Parse each line as json.loads(line) into a dict.
3. Collect into a List[Dict].
4. Delegate to ingest_entities(...).
5. Return the stats.
This provides the JSONL file input mode. The in-memory mode is ingest_entities called directly with [e.to_dict() for e in entities].
3.4. IngestionStats dataclass
@dataclass
class IngestionStats:
    points_uploaded: int = 0
    batches_sent: int = 0
    errors: int = 0
4. Data Flow
                       Mode A (programmatic)
                       ┌──────────────────────┐
                       │ List[ExtractedEntity] │
                       │ → [e.to_dict() ...]   │
                       └──────────┬───────────┘
                                  │
                       Mode B (batch/CLI)
                       ┌──────────┴───────────┐
                       │   .jsonl file on disk │
                       │  (one JSON per line)  │
                       └──────────┬───────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │   List[Dict[str, Any]]   │
                    │   (entity dictionaries)  │
                    └─────────────┬───────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
          ▼                       ▼                       ▼
   generate_point_id()     embed_fn(text)          payload = dict
   uuid5(NS, global_uri)  → List[float]           (all metadata)
          │                       │                       │
          └───────────┬───────────┘───────────────────────┘
                      │
                      ▼
              ┌───────────────┐
              │  PointStruct   │
              │  id=uuid       │
              │  vector=[...]  │
              │  payload={...} │
              └───────┬───────┘
                      │  (batched, 100 per call)
                      ▼
              ┌───────────────┐
              │ client.upsert │
              │  → Qdrant DB  │
              └───────────────┘
5. UUIDv5 Algorithm Detail
import uuid
UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # RFC 4122 DNS
def generate_point_id(global_uri: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, global_uri))
# Example:
# generate_point_id("rtc_engine::test_torture.h::Class::webrtc::rtp_rtcp::RtpEncoder")
# → always returns the same UUID string, e.g. "a3f2b1c4-..."
Idempotency guarantee: Calling client.upsert with the same UUID overwrites the existing point. No duplicates ever created.
6. Qdrant Payload Schema
Each point stored in Qdrant will have this payload structure:
| Payload Key | Type | Source | Indexed |
|---|---|---|---|
| global_uri | string | ExtractedEntity.global_uri | Yes (keyword) |
| repo_name | string | ExtractedEntity.repo_name | Yes (keyword) |
| file_path | string | ExtractedEntity.file_path | Yes (keyword) |
| entity_type | string | ExtractedEntity.entity_type | Yes (keyword) |
| entity_name | string | ExtractedEntity.entity_name | Yes (keyword) |
| docstring | string\|null | ExtractedEntity.docstring | No |
| code_text | string | ExtractedEntity.code_text | No (full text stored) |
| start_line | integer | ExtractedEntity.start_line | No |
| end_line | integer | ExtractedEntity.end_line | No |
| is_templated | boolean | ExtractedEntity.is_templated | No |
7. Testing Strategy
test_qdrant_loader.py
| Test | Category | What It Verifies |
|---|---|---|
| test_generate_point_id_deterministic | Unit | Same global_uri → same UUID every time |
| test_generate_point_id_unique | Unit | Different URIs → different UUIDs |
| test_mock_embedding_dimension | Unit | Output list has correct length |
| test_mock_embedding_deterministic | Unit | Same input text → same vector |
| test_build_point_structure | Unit | PointStruct has correct id, vector length, all payload keys |
| test_build_point_embed_text | Unit | Embedding text is docstring + \n + code_text (or just code_text when docstring is None) |
| test_get_qdrant_client | Integration | Connects to local Qdrant (requires Docker running) |
| test_init_collection_create | Integration | Collection is created with correct dimension |
| test_init_collection_recreate | Integration | Old collection is deleted and recreated |
| test_init_collection_idempotent | Integration | Calling twice without recreate does not error |
| test_ingest_entities | Integration | Points are upserted and retrievable by UUID |
| test_ingest_idempotent | Integration | Re-ingesting same data produces same point count (no duplicates) |
| test_ingest_from_jsonl | Integration | Reads JSONL file, upserts, points are retrievable |
| test_ingest_batch_size | Integration | Large entity list is correctly split into batches |
__main__ block in qdrant_loader.py
For quick manual verification:
1. Import extract_file from extraction.extractor.
2. Parse extraction/tests/fixtures/test_torture.h with repo_name="rtc_engine".
3. Convert to dict list.
4. Call get_qdrant_client().
5. Call init_collection(client, recreate=True).
6. Call ingest_entities(entity_dicts, client).
7. Print stats.
8. Verify by calling client.scroll(collection_name, limit=10) and printing each point's global_uri from payload.
8. Execution Order
1. Create ingestion/ package with __init__.py and config.py.
2. Implement ingestion/embedding.py (mock generator).
3. Implement ingestion/qdrant_loader.py (all 6 functions + IngestionStats + __main__).
4. Write ingestion/tests/test_qdrant_loader.py (unit tests first, then integration).
5. Run unit tests (no Docker required).
6. Run integration tests (Docker must be up).
7. Run python -m ingestion.qdrant_loader for manual end-to-end verification.
