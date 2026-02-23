"""Phase 0 tests: URI contract equivalence between extraction and SCIP mapping."""

import unittest
from pathlib import Path

from extraction.extractor import extract_file
from graphrag.proto import scip_pb2
from graphrag.symbol_mapper import (
    classify_symbol,
    parse_scip_symbol,
    scip_symbol_to_global_uri,
)


class TestUriContractEquivalencePhase0(unittest.TestCase):
    """Validate extraction URI contract against SCIP symbol mapping."""

    def setUp(self) -> None:
        self.repo_name = "contract_repo"
        self.fixtures_dir = Path(__file__).resolve().parents[2] / "extraction" / "tests" / "fixtures"

    def _get_uri_from_extraction(self, file_name: str, entity_name: str) -> str:
        file_path = self.fixtures_dir / file_name
        entities = extract_file(str(file_path), self.repo_name, str(self.fixtures_dir))
        for entity in entities:
            if entity.entity_name == entity_name:
                return entity.global_uri
        raise AssertionError(f"Entity not found: {entity_name} in {file_name}")

    def test_function_uri_equivalence(self) -> None:
        extraction_uri = self._get_uri_from_extraction("simple_function.cpp", "add")
        scip_uri = scip_symbol_to_global_uri(
            scip_symbol="cxx . . $ add(1111).",
            file_path="simple_function.cpp",
            repo_name=self.repo_name,
            kind=0,
        )
        self.assertEqual(extraction_uri, scip_uri)

    def test_namespaced_function_uri_equivalence(self) -> None:
        extraction_uri = self._get_uri_from_extraction(
            "qualified_names.cpp",
            "outer::inner::inner_function",
        )
        scip_uri = scip_symbol_to_global_uri(
            scip_symbol="cxx . . $ outer/inner/inner_function(2222).",
            file_path="qualified_names.cpp",
            repo_name=self.repo_name,
            kind=0,
        )
        self.assertEqual(extraction_uri, scip_uri)

    def test_class_uri_equivalence(self) -> None:
        extraction_uri = self._get_uri_from_extraction("simple_class.h", "Calculator")
        scip_uri = scip_symbol_to_global_uri(
            scip_symbol="cxx . . $ Calculator#",
            file_path="simple_class.h",
            repo_name=self.repo_name,
            kind=0,
        )
        self.assertEqual(extraction_uri, scip_uri)

    def test_struct_kind_from_proto_enum_should_map_to_struct(self) -> None:
        """Protobuf Struct kind should map to entity_type Struct."""
        parsed = parse_scip_symbol(
            scip_symbol="cxx . . $ YAML/Node#",
            kind=scip_pb2.SymbolInformation.Kind.Struct,
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Struct")

    def test_monitored_external_symbol_should_classify_as_stub(self) -> None:
        """Monitored namespace from external package should classify as stub."""
        # This carries a non-local package section and a monitored namespace.
        symbol = "cxx cargo sibling-repo v1.0.0 webrtc/RtpSender#"
        self.assertEqual(classify_symbol(symbol, kind=0), "stub")


if __name__ == "__main__":
    unittest.main()
