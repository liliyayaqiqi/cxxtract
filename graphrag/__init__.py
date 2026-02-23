"""
GraphRAG module — Semantic dependency graph pipeline for C++ codebases.

Public API:
    - run_scip_clang: Generate SCIP index from compile_commands.json
    - parse_scip_index: Parse .scip protobuf into Python objects
    - scip_symbol_to_global_uri: Map SCIP symbols to Global URIs
    - get_neo4j_driver: Connect to Neo4j
    - init_graph_schema: Initialize Neo4j constraints/indexes
    - ingest_graph: Load parsed SCIP data into Neo4j
    - calculate_blast_radius: Query dependency blast radius
    - get_entity_neighbors: Get immediate graph neighbors
    - get_inheritance_tree: Get full inheritance hierarchy
"""

from graphrag.scip_index import run_scip_clang
from graphrag.scip_parser import parse_scip_index, ScipParseResult
from graphrag.symbol_mapper import scip_symbol_to_global_uri, is_external_symbol
from graphrag.neo4j_loader import (
    get_neo4j_driver,
    init_graph_schema,
    clear_repo_graph,
    ingest_graph,
    IngestionStats as GraphIngestionStats,
)
from graphrag.query import (
    calculate_blast_radius,
    get_entity_neighbors,
    get_inheritance_tree,
    BlastRadiusResult,
    AffectedEntity,
    QueryMetadata,
)

__all__ = [
    # SCIP Indexing
    "run_scip_clang",
    # SCIP Parsing
    "parse_scip_index",
    "ScipParseResult",
    # Symbol Mapping
    "scip_symbol_to_global_uri",
    "is_external_symbol",
    # Neo4j Connection
    "get_neo4j_driver",
    "init_graph_schema",
    "clear_repo_graph",
    # Graph Ingestion
    "ingest_graph",
    "GraphIngestionStats",
    # Queries
    "calculate_blast_radius",
    "get_entity_neighbors",
    "get_inheritance_tree",
    "BlastRadiusResult",
    "AffectedEntity",
    "QueryMetadata",
]
