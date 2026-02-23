"""Phase 5 tests for SCIP local-definition semantics and scope performance."""

import tempfile
import unittest
from pathlib import Path

from graphrag.proto import scip_pb2
from graphrag.scip_parser import (
    _build_enclosing_scope_map,
    _collect_index_definition_symbols,
    parse_scip_index,
    parse_scip_index_stream,
)
from graphrag.symbol_mapper import classify_symbol


class TestLocalDefinitionByOccurrence(unittest.TestCase):
    """Ensure local-definition context is derived from Definition occurrences."""

    def _build_index_path(self) -> str:
        index = scip_pb2.Index()
        index.metadata.version = scip_pb2.UnspecifiedProtocolVersion
        index.metadata.tool_info.name = "phase5-local-def-test"
        index.metadata.tool_info.version = "1.0.0"
        index.metadata.project_root = "file:///phase5"
        index.metadata.text_document_encoding = scip_pb2.UTF8

        doc = index.documents.add()
        doc.language = "cpp"
        doc.relative_path = "src/test.cpp"
        doc.position_encoding = scip_pb2.UTF8CodeUnitOffsetFromLineStart

        local_symbol = "cxx . . $ YAML/Wrapper#"
        external_symbol = "cxx cargo sibling-repo v1.0.0 webrtc/RtpSender#"

        local_info = doc.symbols.add()
        local_info.symbol = local_symbol
        local_info.kind = scip_pb2.SymbolInformation.Kind.Class
        local_info.display_name = "Wrapper"

        external_info = doc.symbols.add()
        external_info.symbol = external_symbol
        external_info.kind = scip_pb2.SymbolInformation.Kind.Class
        external_info.display_name = "RtpSender"

        local_def = doc.occurrences.add()
        local_def.symbol = local_symbol
        local_def.range.extend([0, 0, 1, 1])
        local_def.enclosing_range.extend([0, 0, 1, 1])
        local_def.symbol_roles = 0x1  # Definition

        external_ref = doc.occurrences.add()
        external_ref.symbol = external_symbol
        external_ref.range.extend([3, 2, 3, 10])
        external_ref.symbol_roles = 0x8  # ReadAccess

        handle = tempfile.NamedTemporaryFile(suffix=".scip", delete=False)
        handle.write(index.SerializeToString())
        handle.flush()
        handle.close()
        return handle.name

    def test_reference_only_symbol_is_stub_and_definition_symbol_is_keep(self) -> None:
        index_path = self._build_index_path()
        try:
            with open(index_path, "rb") as f:
                idx = scip_pb2.Index()
                idx.ParseFromString(f.read())

            local_symbol = "cxx . . $ YAML/Wrapper#"
            external_symbol = "cxx cargo sibling-repo v1.0.0 webrtc/RtpSender#"
            local_defs = _collect_index_definition_symbols(idx)

            self.assertIn(local_symbol, local_defs)
            self.assertNotIn(external_symbol, local_defs)
            self.assertEqual(
                classify_symbol(
                    local_symbol,
                    kind=scip_pb2.SymbolInformation.Kind.Class,
                    is_local_definition=(local_symbol in local_defs),
                ),
                "keep",
            )
            self.assertEqual(
                classify_symbol(
                    external_symbol,
                    kind=scip_pb2.SymbolInformation.Kind.Class,
                    is_local_definition=(external_symbol in local_defs),
                ),
                "stub",
            )

            result = parse_scip_index(index_path, repo_name="phase5")
            self.assertTrue(any(s.scip_symbol == local_symbol for s in result.symbols))
            self.assertTrue(any(s.scip_symbol == external_symbol for s in result.symbols))
            local_node = next(s for s in result.symbols if s.scip_symbol == local_symbol)
            external_node = next(s for s in result.symbols if s.scip_symbol == external_symbol)
            self.assertEqual(local_node.disposition, "keep")
            self.assertEqual(external_node.disposition, "stub")
            self.assertTrue(any(r.scip_symbol == external_symbol for r in result.references))
        finally:
            Path(index_path).unlink(missing_ok=True)


class TestEnclosingScopeSweepLine(unittest.TestCase):
    """Ensure enclosing scope attribution uses sparse line queries efficiently."""

    def test_nested_spans_with_sparse_queries(self) -> None:
        doc = scip_pb2.Document()
        doc.language = "cpp"
        doc.relative_path = "src/huge.cpp"
        doc.position_encoding = scip_pb2.UTF8CodeUnitOffsetFromLineStart

        outer = doc.occurrences.add()
        outer.symbol = "cxx . . $ YAML/outer(aaaa)."
        outer.range.extend([0, 0, 1_000_000, 1])
        outer.enclosing_range.extend([0, 0, 1_000_000, 1])
        outer.symbol_roles = 0x1

        inner = doc.occurrences.add()
        inner.symbol = "cxx . . $ YAML/inner(bbbb)."
        inner.range.extend([50, 0, 60, 1])
        inner.enclosing_range.extend([50, 0, 60, 1])
        inner.symbol_roles = 0x1

        scope_map = _build_enclosing_scope_map(doc, reference_lines={55, 70, 999_999})
        self.assertEqual(len(scope_map), 3)
        self.assertEqual(scope_map[55], "cxx . . $ YAML/inner(bbbb).")
        self.assertEqual(scope_map[70], "cxx . . $ YAML/outer(aaaa).")
        self.assertEqual(scope_map[999_999], "cxx . . $ YAML/outer(aaaa).")


class TestStreamingParser(unittest.TestCase):
    def _build_multi_doc_index(self, doc_count: int = 3) -> str:
        index = scip_pb2.Index()
        index.metadata.version = scip_pb2.UnspecifiedProtocolVersion
        index.metadata.tool_info.name = "phase5-stream-test"
        index.metadata.tool_info.version = "1.0.0"
        index.metadata.project_root = "file:///phase5"
        index.metadata.text_document_encoding = scip_pb2.UTF8

        for i in range(doc_count):
            doc = index.documents.add()
            doc.language = "cpp"
            doc.relative_path = f"src/file_{i}.cpp"
            doc.position_encoding = scip_pb2.UTF8CodeUnitOffsetFromLineStart

            symbol = f"cxx . . $ YAML/Thing{i}#"
            sym_info = doc.symbols.add()
            sym_info.symbol = symbol
            sym_info.kind = scip_pb2.SymbolInformation.Kind.Class
            sym_info.display_name = f"Thing{i}"

            occ_def = doc.occurrences.add()
            occ_def.symbol = symbol
            occ_def.range.extend([0, 0, 0, 4])
            occ_def.enclosing_range.extend([0, 0, 0, 4])
            occ_def.symbol_roles = 0x1

            occ_ref = doc.occurrences.add()
            occ_ref.symbol = symbol
            occ_ref.range.extend([1, 0, 1, 4])
            occ_ref.symbol_roles = 0x8

        handle = tempfile.NamedTemporaryFile(suffix=".scip", delete=False)
        handle.write(index.SerializeToString())
        handle.flush()
        handle.close()
        return handle.name

    def test_streaming_batches_and_compat_result(self) -> None:
        index_path = self._build_multi_doc_index(doc_count=3)
        try:
            batches = list(
                parse_scip_index_stream(
                    index_path,
                    repo_name="phase5",
                    batch_documents=2,
                )
            )
            self.assertEqual(len(batches), 2)
            self.assertEqual([b.document_count for b in batches], [2, 1])
            self.assertEqual(sum(len(b.symbols) for b in batches), 3)
            self.assertEqual(sum(len(b.references) for b in batches), 3)

            compat = parse_scip_index(index_path, repo_name="phase5")
            self.assertEqual(compat.document_count, 3)
            self.assertEqual(len(compat.symbols), 3)
            self.assertEqual(len(compat.references), 3)
        finally:
            Path(index_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
