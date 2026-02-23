"""Phase 3 tests: graph semantics, stub handling, and ingestion invariants."""

import unittest

from core.uri_contract import create_global_uri
from graphrag.neo4j_loader import (
    GraphEdge,
    GraphNode,
    _build_edges_from_relationships,
    _dedupe_edges,
    _dedupe_nodes,
    _infer_implementation_edge_type,
    _validate_edges,
)
from graphrag.proto import scip_pb2
from graphrag.scip_parser import ScipRelationship, ScipSymbolDef


class TestPhase3ImplementationEdgeTyping(unittest.TestCase):
    """Validate INHERITS/OVERRIDES inference from SCIP relationship semantics."""

    def test_class_implementation_maps_to_inherits(self) -> None:
        rel_type = _infer_implementation_edge_type(
            src_symbol="cxx . . $ YAML/Dog#",
            src_kind=scip_pb2.SymbolInformation.Kind.Class,
            target_symbol="cxx . . $ YAML/Animal#",
            target_kind=scip_pb2.SymbolInformation.Kind.Class,
        )
        self.assertEqual(rel_type, "INHERITS")

    def test_method_implementation_maps_to_overrides(self) -> None:
        rel_type = _infer_implementation_edge_type(
            src_symbol="cxx . . $ YAML/Dog#sound(aaaa).",
            src_kind=scip_pb2.SymbolInformation.Kind.Method,
            target_symbol="cxx . . $ YAML/Animal#sound(bbbb).",
            target_kind=scip_pb2.SymbolInformation.Kind.Method,
        )
        self.assertEqual(rel_type, "OVERRIDES")

    def test_invalid_implementation_pair_is_dropped(self) -> None:
        rel_type = _infer_implementation_edge_type(
            src_symbol="cxx . . $ YAML/run(aaaa).",
            src_kind=scip_pb2.SymbolInformation.Kind.Function,
            target_symbol="cxx . . $ YAML/Animal#",
            target_kind=scip_pb2.SymbolInformation.Kind.Class,
        )
        self.assertIsNone(rel_type)


class TestPhase3StubAndDedup(unittest.TestCase):
    """Validate cross-repo stub creation and dedup behavior."""

    def test_cross_repo_relationship_creates_stub_node(self) -> None:
        src_symbol = "cxx . . $ YAML/Widget#"
        target_symbol = "cxx . . $ webrtc/RtpSender#"

        symbols = [
            ScipSymbolDef(
                scip_symbol=src_symbol,
                file_path="src/widget.h",
                kind=scip_pb2.SymbolInformation.Kind.Class,
                display_name="Widget",
                relationships=[
                    ScipRelationship(
                        target_symbol=target_symbol,
                        is_reference=False,
                        is_implementation=True,
                        is_type_definition=False,
                        is_definition=False,
                    )
                ],
            )
        ]

        stub_nodes: list[GraphNode] = []
        edges = _build_edges_from_relationships(
            symbols=symbols,
            repo_name="phase3-repo",
            stub_nodes=stub_nodes,
            symbol_file_map={src_symbol: "src/widget.h"},
            symbol_kind_map={src_symbol: scip_pb2.SymbolInformation.Kind.Class},
        )

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].relationship_type, "INHERITS")
        self.assertEqual(len(stub_nodes), 1)
        self.assertTrue(stub_nodes[0].is_external)
        self.assertEqual(stub_nodes[0].file_path, "<external>")
        self.assertEqual(stub_nodes[0].global_uri, edges[0].tgt_uri)

    def test_dedupe_nodes_prefers_local_node_over_stub(self) -> None:
        uri = create_global_uri("repo", "src/a.h", "Class", "YAML::A")
        nodes = [
            GraphNode(
                global_uri=uri,
                repo_name="repo",
                file_path="<external>",
                entity_type="Class",
                entity_name="YAML::A",
                scip_symbol="cxx . . $ YAML/A#",
                is_external=True,
            ),
            GraphNode(
                global_uri=uri,
                repo_name="repo",
                file_path="src/a.h",
                entity_type="Class",
                entity_name="YAML::A",
                scip_symbol="cxx . . $ YAML/A#",
                is_external=False,
            ),
        ]
        deduped = _dedupe_nodes(nodes)
        self.assertEqual(len(deduped), 1)
        self.assertFalse(deduped[0].is_external)
        self.assertEqual(deduped[0].file_path, "src/a.h")

    def test_dedupe_edges_removes_duplicates(self) -> None:
        src = create_global_uri("repo", "src/a.cpp", "Function", "YAML::a")
        tgt = create_global_uri("repo", "src/b.cpp", "Function", "YAML::b")
        edges = [
            GraphEdge(src_uri=src, tgt_uri=tgt, relationship_type="CALLS"),
            GraphEdge(src_uri=src, tgt_uri=tgt, relationship_type="CALLS"),
        ]
        deduped = _dedupe_edges(edges)
        self.assertEqual(len(deduped), 1)


class TestPhase3Invariants(unittest.TestCase):
    """Validate edge invariant checks applied before write batches."""

    def test_validate_edges_filters_invalid_pairs(self) -> None:
        valid_src = create_global_uri("repo", "src/a.cpp", "Function", "YAML::run")
        valid_tgt = create_global_uri("repo", "src/b.cpp", "Function", "YAML::work")
        bad_tgt = create_global_uri("repo", "src/c.h", "Struct", "YAML::Node")
        file_src = create_global_uri("repo", "src/a.cpp", "File", "src/a.cpp")

        edges = [
            GraphEdge(src_uri=valid_src, tgt_uri=valid_tgt, relationship_type="CALLS"),
            GraphEdge(src_uri=valid_src, tgt_uri=bad_tgt, relationship_type="CALLS"),
            GraphEdge(src_uri=file_src, tgt_uri=valid_tgt, relationship_type="CALLS"),
        ]
        filtered = _validate_edges(edges)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].src_uri, valid_src)
        self.assertEqual(filtered[0].tgt_uri, valid_tgt)


if __name__ == "__main__":
    unittest.main()
