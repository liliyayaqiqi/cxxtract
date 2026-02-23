"""Phase 3 tests: graph semantics, stub handling, and ingestion invariants."""

import unittest
from unittest.mock import patch

from core.uri_contract import create_global_uri
from graphrag.neo4j_loader import (
    GraphEdge,
    GraphNode,
    _build_edges_from_relationships,
    _build_nodes_from_symbols,
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

    def test_overloaded_function_symbols_do_not_merge_by_uri(self) -> None:
        symbols = [
            ScipSymbolDef(
                scip_symbol="cxx . . $ YAML/add(aaaa1111).",
                file_path="src/math.cpp",
                kind=scip_pb2.SymbolInformation.Kind.Function,
                display_name="add(int)",
            ),
            ScipSymbolDef(
                scip_symbol="cxx . . $ YAML/add(bbbb2222).",
                file_path="src/math.cpp",
                kind=scip_pb2.SymbolInformation.Kind.Function,
                display_name="add(double)",
            ),
        ]

        nodes = _build_nodes_from_symbols(symbols, repo_name="phase3-repo")
        self.assertEqual(len(nodes), 2)
        self.assertEqual(len({n.global_uri for n in nodes}), 1)
        self.assertEqual(len({n.identity_key for n in nodes}), 2)
        self.assertEqual(len({n.function_sig_hash for n in nodes}), 2)
        self.assertEqual(len(_dedupe_nodes(nodes)), 2)

    def test_cross_repo_stub_completes_with_owner_repo_uri(self) -> None:
        src_symbol = "cxx . . $ YAML/Widget#"
        target_symbol = "cxx . . $ webrtc/RtpSender#"

        with patch.dict(
            "graphrag.symbol_mapper.MONITORED_NAMESPACE_OWNER_REPOS",
            {"webrtc": "repo-b"},
            clear=True,
        ):
            stub_nodes: list[GraphNode] = []
            _build_edges_from_relationships(
                symbols=[
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
                ],
                repo_name="repo-a",
                stub_nodes=stub_nodes,
                symbol_file_map={src_symbol: "src/widget.h"},
                symbol_kind_map={
                    src_symbol: scip_pb2.SymbolInformation.Kind.Class,
                    target_symbol: scip_pb2.SymbolInformation.Kind.Class,
                },
            )

            self.assertEqual(len(stub_nodes), 1)
            stub = stub_nodes[0]
            self.assertEqual(stub.repo_name, "repo-b")
            self.assertEqual(stub.owner_repo, "repo-b")
            self.assertEqual(stub.ingestion_repo, "repo-a")

            owner_nodes = _build_nodes_from_symbols(
                symbols=[
                    ScipSymbolDef(
                        scip_symbol=target_symbol,
                        file_path="<external>",
                        kind=scip_pb2.SymbolInformation.Kind.Class,
                        display_name="RtpSender",
                    )
                ],
                repo_name="repo-b",
            )
            self.assertEqual(len(owner_nodes), 1)
            self.assertEqual(stub.global_uri, owner_nodes[0].global_uri)

            deduped = _dedupe_nodes(stub_nodes + owner_nodes)
            self.assertEqual(len(deduped), 1)
            self.assertFalse(deduped[0].is_external)
            self.assertEqual(deduped[0].repo_name, "repo-b")

    def test_stub_symbol_definition_uses_owner_repo_uri(self) -> None:
        with patch.dict(
            "graphrag.symbol_mapper.MONITORED_NAMESPACE_OWNER_REPOS",
            {"webrtc": "repo-b"},
            clear=True,
        ):
            nodes = _build_nodes_from_symbols(
                symbols=[
                    ScipSymbolDef(
                        scip_symbol="cxx cargo sibling-repo v1.0.0 webrtc/RtpSender#",
                        file_path="src/placeholder.cpp",
                        kind=scip_pb2.SymbolInformation.Kind.Class,
                        display_name="RtpSender",
                        disposition="stub",
                    )
                ],
                repo_name="repo-a",
            )
            self.assertEqual(len(nodes), 1)
            node = nodes[0]
            self.assertTrue(node.is_external)
            self.assertEqual(node.repo_name, "repo-b")
            self.assertEqual(node.owner_repo, "repo-b")
            self.assertEqual(node.ingestion_repo, "repo-a")
            self.assertTrue(node.global_uri.startswith("repo-b::"))

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
