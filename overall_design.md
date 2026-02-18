# Architecture Blueprint: AI-Native Code Context Engine

## 1. System Philosophy: The "Dual-Brain" Architecture

To serve vastly different AI personas—from a probabilistic Chatbot looking for concepts, to a deterministic CI/CD Agent mathematically verifying PR safety—the system cannot rely on a single database.

To prevent massive technical debt, this architecture strictly decouples **how code is extracted** (Layer 1), **how it is stored** (Layer 2), and **how agents access it** (Layer 3).

---

## 2. Layer 1: The Extraction Engine (Data Pipelines)

Naive AI applications chunk text arbitrarily (e.g., cutting a file every 500 words). This destroys code logic. Our ingestion layer uses structure-aware parsing triggered automatically by your Git pipelines.

* **The Global URI Protocol:** Every extracted entity is assigned a strict, deterministic ID linking all databases together.
* *Format:* `[RepoName]::[FilePath]::[EntityType]::[EntityName]`
* *Example:* `billing-api::src/invoice.ts::Function::calculateTax`


* **Syntactic Parsing (Tree-sitter):**
* *Trigger:* Runs in milliseconds on every Git `push`.
* *Function:* Parses raw source files into Abstract Syntax Trees (ASTs). It logically isolates individual functions, classes, and their attached docstrings without breaking their boundaries.
* *Output:* Passes the perfectly chunked text and the Global URI to the Vector Database.


* **Semantic Parsing (SCIP - Semantic Code Intelligence Protocol):**
* *Trigger:* Runs as a background CI/CD job upon merging to the `main` branch.
* *Function:* Hooks directly into the native language compiler (e.g., `tsc` for TypeScript). It reads the entire workspace to mathematically resolve all cross-file and cross-repository imports.
* *Output:* A static `.scip` file mapping every definition to every reference. It passes this topology to the Graph Database.

---

## 3. Layer 2: The Dual-Storage Engine (The "Databases")

AI agents ask two fundamentally different types of questions. The data is routed into two specialized databases optimized for different physical memory layouts.

### A. The Hybrid Search DB (Probabilistic Search)

* **Technology:** Milvus, Qdrant, or PostgreSQL (with `pgvector` + `pg_trgm`).
* **What it stores:** The raw AST code chunks, docstrings, and their high-dimensional Vector Embeddings.
* **How it works:**
* *Vector Search (HNSW Layout):* Groups code mathematically by concept. A Chatbot asks, *"Where is the payment logic?"*, and the DB returns `processStripe()` even without an exact keyword match.
* *Lexical Search (BM25 Layout):* A sparse index for exact string matching. A Debugger Agent asks, *"Where is ERR_502_DB thrown?"*, and the DB instantly returns the exact line.



### B. The Dependency Graph DB (Deterministic Traversal)

* **Technology:** Neo4j or Memgraph.
* **What it stores:** Graph Primitives. Nodes (Functions, API endpoints, Jira Tickets) and Edges (Relationships like `CALLS`, `IMPLEMENTS`). *It does not store raw code bodies.*
* **How it works:** Uses **Index-Free Adjacency**. It stores relationships as literal physical memory pointers on the SSD. To calculate a deep dependency chain, it simply follows memory addresses ( traversal speed). This allows a CI/CD agent to instantly calculate a 10-hop cross-repo blast radius without hallucinating.

---

## 4. Layer 3: The Universal Access Gateway (The API)

Agents should **never** query the databases directly using custom SQL or Cypher prompts, as LLMs will frequently hallucinate the database syntax.

* **Technology:** Model Context Protocol (MCP) Server.
* **Implementation:** You deploy a lightweight Python/FastAPI server that securely connects to both databases. It acts as the "USB-C port" for your knowledge base.
* **Exposed Tools:** Any authorized agent (Cloud Orchestrator or Local IDE) can connect to the MCP server and autonomously call strictly typed tools:
* `semantic_search(intent: str)`  Routes to Vector DB.
* `exact_keyword_search(keyword: str)`  Routes to BM25 Lexical DB.
* `calculate_blast_radius(global_uri: str)`  Routes to Graph DB.
* `read_file_chunk(global_uri: str)`  Fetches exact code for the LLM's context window.



---

## 5. End-to-End Workflow: How the Agents Use the Infrastructure

Here is how this architecture powers the fully automated SDLC workflow you originally envisioned:

1. **Planning (The Design Spec Agent):**
A Product Manager writes a requirement. The cloud-based Spec Agent connects to the MCP Server. It uses `semantic_search` to find existing components and `calculate_blast_radius` to see what cross-repo APIs currently exist. It outputs a highly accurate, machine-readable `pr_plan.yaml` containing the exact target files.
2. **Implementation (The Local Coding Agent):**
The human developer opens their AI-Native IDE (Cursor/Windsurf). **Crucial Handoff:** The local IDE does *not* query the cloud Knowledge Base. It reads the `pr_plan.yaml`. The local AI agent uses the editor's live **LSP (Language Server Protocol)** to rapidly write, type-check, and self-correct the code in real-time on the developer's laptop.
3. **Gatekeeping (The CI/CD Review Agent):**
The developer submits the Pull Request. The cloud Review Agent wakes up, extracts the modified function URIs, and queries the **Graph DB** (via MCP) to mathematically verify that the new code does not violate upstream contracts in other repositories. If it fails, it rejects the PR and provides a fix plan.
4. **Continuous Sync (The Knowledge FAQ Chatbot):**
Once merged, the Git webhook fires. The background workers run Tree-sitter and SCIP, instantly updating the Vector and Graph databases. When a developer asks the Slack Chatbot about the new feature 5 minutes later, it answers perfectly—achieving true **Code-as-Document** synchronization.

### Recommended Tech Stack Summary

| Architecture Layer | Component | Recommended Technology |
| --- | --- | --- |
| **Layer 1: Extraction** | Syntactic Chunker | **Tree-sitter** |
|  | Semantic Mapper | **SCIP Indexers** (`scip-typescript`, etc.) |
| **Layer 2: Storage** | Vector/Hybrid DB | **Milvus** or **Qdrant** |
|  | Graph/Topology DB | **Neo4j** |
| **Layer 3: Access** | Universal Agent API | **Model Context Protocol (MCP)** Server |
| **Layer 4: Application** | Local Code Validation | **Language Server Protocol (LSP)** |