OpenRouter Embedding Integration — Implementation Plan
0. Scope & Constraint
This plan is strictly limited to the embedding generation and ingestion data path. No chat, retrieval, summarization, or RAG query logic is in scope.
1. Files Modified
| File | Change Type |
|---|---|
| requirements.txt | Add 3 new dependencies |
| .env | Create (gitignored) — holds OPENROUTER_API_KEY |
| .env.example | Create — committed template without secrets |
| ingestion/config.py | Add OpenRouter constants, dotenv loading |
| ingestion/embedding.py | Add generate_real_embedding, get_embedding router, text truncation |
| ingestion/qdrant_loader.py | Change default embed_fn from generate_mock_embedding to get_embedding |
| ingestion/__init__.py | Export get_embedding |
| run_pipeline.py | No changes required (already delegates to qdrant_loader defaults) |
2. Dependencies (requirements.txt)
Add the following block:
# Layer 1: Embedding Generation
openai>=1.0.0
python-dotenv>=1.0.0
tenacity>=8.2.0
- openai — OpenAI-compatible client, used to call OpenRouter's /v1/embeddings endpoint.
- python-dotenv — Loads OPENROUTER_API_KEY from .env file at import time.
- tenacity — Provides @retry decorator with exponential backoff for rate-limit and transient errors.
3. Environment Files
.env (gitignored, never committed)
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxx
.env.example (committed, template for developers)
OPENROUTER_API_KEY=your_openrouter_api_key_here
4. Configuration (ingestion/config.py)
Changes
Add at the top of the file:
import os
from dotenv import load_dotenv
load_dotenv()  # Load .env from project root
Add a new section after the existing Qdrant config:
| Constant | Type | Source | Default |
|---|---|---|---|
| OPENROUTER_API_KEY | str | os.environ.get("OPENROUTER_API_KEY", "") | "" (empty = will fail on real calls) |
| OPENROUTER_BASE_URL | str | Hardcoded | "https://openrouter.ai/api/v1" |
| EMBEDDING_MODEL | str | os.environ.get("EMBEDDING_MODEL", ...) | "openai/text-embedding-3-small" |
| USE_MOCK_EMBEDDING | bool | os.environ.get("USE_MOCK_EMBEDDING", ...).lower() == "true" | False |
| MAX_EMBED_TOKENS | int | Hardcoded | 8191 (context limit for text-embedding-3-small) |
| EMBEDDING_MAX_RETRIES | int | Hardcoded | 5 |
Validation Logic
If USE_MOCK_EMBEDDING is False and OPENROUTER_API_KEY is empty, log a warning at import time but do NOT crash. The crash should happen at call time (generate_real_embedding) with a clear error message. This prevents the module from being un-importable during testing.
5. Embedding Module (ingestion/embedding.py)
Existing Function — NO CHANGES
def generate_mock_embedding(text: str, dimension: int = 1536) -> List[float]:
    # ... completely untouched ...
New Function 1: truncate_text
def truncate_text(text: str, max_tokens: int = MAX_EMBED_TOKENS) -> str:
Algorithm:
1. Rough approximation: 1 token ~= 4 characters for code (conservative).
2. max_chars = max_tokens * 4
3. If len(text) <= max_chars, return text unchanged.
4. Otherwise, truncate to text[:max_chars] and log a warning with the original length.
Why not use tiktoken: Adding a tokenizer dependency for a safety truncation is overkill. The 4:1 ratio is conservative (under-counts tokens), which means we'll truncate slightly earlier than necessary — safe, never over the limit.
New Function 2: generate_real_embedding
def generate_real_embedding(text: str, dimension: int = 1536) -> List[float]:
Algorithm:
1. Import and use OpenAI client from openai package, initialized with:
   - api_key=OPENROUTER_API_KEY
   - base_url=OPENROUTER_BASE_URL
2. Guard: if OPENROUTER_API_KEY is empty, raise ValueError("OPENROUTER_API_KEY not set...").
3. Call truncate_text(text) to enforce token limit.
4. Wrap the API call with tenacity.retry:
   - Retry on: openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError.
   - Strategy: wait_exponential(multiplier=1, min=2, max=30).
   - Stop: stop_after_attempt(EMBEDDING_MAX_RETRIES).
   - Before sleep: log the retry attempt number and wait time.
5. Make the API call:
      response = client.embeddings.create(
       model=EMBEDDING_MODEL,
       input=text,
       dimensions=dimension
   )
   6. Extract: return response.data[0].embedding
Client Initialization: The OpenAI client should be created as a module-level singleton (lazy-initialized on first call) to avoid creating a new HTTP connection pool per embedding call. This is critical for batch performance.
_openai_client: Optional[OpenAI] = None
def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL
        )
    return _openai_client
New Function 3: get_embedding (Router)
def get_embedding(text: str, dimension: int = 1536) -> List[float]:
Algorithm:
1. If USE_MOCK_EMBEDDING is True, call generate_mock_embedding(text, dimension).
2. Otherwise, call generate_real_embedding(text, dimension).
3. Return the result.
This is the single entry point the rest of the codebase uses. Nobody calls the mock or real functions directly except tests.
6. Integration Changes
qdrant_loader.py
Single change — line 30 and line 321-322:
Current:
from ingestion.embedding import generate_mock_embedding
...
if embed_fn is None:
    embed_fn = generate_mock_embedding
New:
from ingestion.embedding import get_embedding
...
if embed_fn is None:
    embed_fn = get_embedding
This is the only change needed in qdrant_loader.py. The function signature of get_embedding(text, dimension) matches the existing Callable[[str, int], List[float]] type annotation.
ingestion/__init__.py
Add get_embedding to imports and __all__.
run_pipeline.py
No changes needed. It already delegates to ingest_from_jsonl which calls ingest_entities which uses the default embed_fn. The routing happens automatically via get_embedding → USE_MOCK_EMBEDDING flag.
7. Execution Order
| Step | Task | Files |
|---|---|---|
| 1 | Update requirements.txt, run pip install | requirements.txt |
| 2 | Create .env and .env.example | .env, .env.example |
| 3 | Update ingestion/config.py with OpenRouter constants | config.py |
| 4 | Update ingestion/embedding.py with truncate_text, generate_real_embedding, get_embedding | embedding.py |
| 5 | Update qdrant_loader.py default import | qdrant_loader.py |
| 6 | Update ingestion/__init__.py exports | __init__.py |
| 7 | Run existing tests (must all pass with USE_MOCK_EMBEDDING=true) | All test files |
| 8 | Write and run new unit tests for embedding module | test_embedding.py |
| 9 | Manual end-to-end verification with real API key | run_pipeline.py |
8. Testing Strategy
8.1 Existing Tests — Must Not Break
All 102 existing tests must continue to pass. The key invariant: when USE_MOCK_EMBEDDING=true (or when tests explicitly pass generate_mock_embedding as embed_fn), behavior is identical to current.
8.2 New Unit Tests (ingestion/tests/test_embedding.py)
| Test | What It Verifies |
|---|---|
| test_get_embedding_routes_to_mock | Set USE_MOCK_EMBEDDING=True via monkeypatch. Call get_embedding. Assert output matches generate_mock_embedding output. |
| test_get_embedding_routes_to_real | Monkeypatch USE_MOCK_EMBEDDING=False and mock the OpenAI client. Assert generate_real_embedding is called. |
| test_truncate_text_short | Text under limit → returned unchanged. |
| test_truncate_text_long | Text over limit → truncated to MAX_EMBED_TOKENS * 4 chars. |
| test_truncate_text_exact_boundary | Text exactly at limit → returned unchanged. |
| test_real_embedding_no_api_key | OPENROUTER_API_KEY="" → generate_real_embedding raises ValueError. |
| test_real_embedding_dimension | Mock OpenAI client. Verify dimensions=dimension is passed to API call. |
| test_real_embedding_retry_on_rate_limit | Mock OpenAI to raise RateLimitError twice then succeed. Verify 3 calls made, result returned. |
| test_openai_client_singleton | Call _get_openai_client() twice. Assert same object returned (not recreated). |
| test_mock_embedding_unchanged | Existing mock tests still pass (regression guard). |
8.3 Manual QA Scenarios
Scenario A: Mock mode (fast, free)
USE_MOCK_EMBEDDING=true python run_pipeline.py \
  --source-dir extraction/tests/fixtures \
  --repo-name rtc-engine \
  --recreate-collection
Expected: Pipeline completes instantly, 36 entities ingested with mock vectors. Identical to current behavior.
Scenario B: Real mode (requires API key)
python run_pipeline.py \
  --source-dir extraction/tests/fixtures \
  --repo-name rtc-engine \
  --recreate-collection
Expected: Pipeline calls OpenRouter API for each entity. Logs show real HTTP calls. Qdrant receives real embedding vectors. Verify one vector is NOT all random by retrieving a point and checking vector values differ from mock output.
Scenario C: Missing API key guard
OPENROUTER_API_KEY="" USE_MOCK_EMBEDDING=false python run_pipeline.py \
  --source-dir extraction/tests/fixtures \
  --repo-name rtc-engine
Expected: Pipeline fails with clear ValueError: OPENROUTER_API_KEY not set message during Phase 2, not at import time.
9. Data Flow Diagram (Post-Upgrade)
                        ┌──────────────────────┐
                        │  ingestion/config.py  │
                        │                       │
                        │  USE_MOCK_EMBEDDING   │
                        │  OPENROUTER_API_KEY   │
                        │  EMBEDDING_MODEL      │
                        └──────────┬────────────┘
                                   │
                        ┌──────────▼────────────┐
                        │  get_embedding(text)   │  ← single entry point
                        │                        │
                        │  if USE_MOCK_EMBEDDING │
                        │    ├─ True ──────────► generate_mock_embedding()
                        │    │                   (deterministic, free, fast)
                        │    └─ False ─────────► generate_real_embedding()
                        │                        │
                        └────────────────────────┘
                                                 │
                                   ┌─────────────▼──────────────┐
                                   │ generate_real_embedding()   │
                                   │                             │
                                   │  1. truncate_text(text)     │
                                   │  2. _get_openai_client()    │ ← singleton
                                   │  3. @retry(exponential)     │
                                   │  4. client.embeddings.create│
                                   │     model=EMBEDDING_MODEL   │
                                   │     dimensions=dimension    │
                                   │     base_url=OPENROUTER     │
                                   └─────────────┬──────────────┘
                                                 │
                                                 ▼
                                          List[float]
                                                 │
                                   ┌─────────────▼──────────────┐
                                   │     build_point()           │
                                   │     → PointStruct           │
                                   │     → client.upsert()       │
                                   │     → Qdrant                │
                                   └────────────────────────────┘
