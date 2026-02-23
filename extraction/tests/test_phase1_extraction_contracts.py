"""Phase 1 extraction contract tests (identity + policy + diagnostics)."""

import tempfile
import unittest
from pathlib import Path

from core.uri_contract import normalize_cpp_entity_name
from extraction.extractor import extract_directory
from extraction.parser import parse_bytes
from extraction.traversal import extract_entities_from_tree


class TestNameNormalizationPhase1(unittest.TestCase):
    """Validate canonical entity-name normalization."""

    def test_normalize_scope_operator_spacing(self) -> None:
        self.assertEqual(
            normalize_cpp_entity_name("TcpServerController ::~TcpServerController"),
            "TcpServerController::~TcpServerController",
        )

    def test_normalize_extra_whitespace(self) -> None:
        self.assertEqual(
            normalize_cpp_entity_name(" outer ::  inner :: value "),
            "outer::inner::value",
        )


class TestDoxygenCleaningPhase1(unittest.TestCase):
    """Validate delimiter-safe Doxygen cleaning."""

    def test_single_line_block_comment_cleaned(self) -> None:
        source = b"""
/** Inline API doc */
void foo() {}
"""
        tree = parse_bytes(source)
        entities = extract_entities_from_tree(tree, source, "repo", "file.cpp")
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].docstring, "Inline API doc")


class TestDeclarationPolicyPhase1(unittest.TestCase):
    """Validate declaration extraction policy switches."""

    def test_default_skips_declarations(self) -> None:
        source = b"""
void proto_only();
void impl() {}
extern "C" {
  void c_api();
}
"""
        tree = parse_bytes(source)
        entities = extract_entities_from_tree(
            tree=tree,
            source_bytes=source,
            repo_name="repo",
            file_path="policy.cpp",
        )
        names = [entity.entity_name for entity in entities]
        self.assertIn("impl", names)
        self.assertNotIn("proto_only", names)
        self.assertNotIn("c_api", names)

    def test_include_declarations_adds_all_prototypes(self) -> None:
        source = b"""
void proto_only();
void impl() {}
extern "C" {
  void c_api();
}
"""
        tree = parse_bytes(source)
        entities = extract_entities_from_tree(
            tree=tree,
            source_bytes=source,
            repo_name="repo",
            file_path="policy.cpp",
            include_declarations=True,
            extern_c_declarations=False,
        )
        names = [entity.entity_name for entity in entities]
        self.assertIn("impl", names)
        self.assertIn("proto_only", names)
        self.assertIn("c_api", names)

    def test_extern_c_only_policy(self) -> None:
        source = b"""
void proto_only();
void impl() {}
extern "C" {
  void c_api();
}
"""
        tree = parse_bytes(source)
        entities = extract_entities_from_tree(
            tree=tree,
            source_bytes=source,
            repo_name="repo",
            file_path="policy.cpp",
            include_declarations=False,
            extern_c_declarations=True,
        )
        names = [entity.entity_name for entity in entities]
        self.assertIn("impl", names)
        self.assertNotIn("proto_only", names)
        self.assertIn("c_api", names)


class TestParseErrorMetricsPhase1(unittest.TestCase):
    """Validate parse error metrics are tracked by AST error-node inspection."""

    def test_extract_directory_populates_parse_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "ok.cpp").write_text("void ok() {}", encoding="utf-8")
            (tmp_path / "broken.cpp").write_text("void broken( {", encoding="utf-8")

            _, stats = extract_directory(str(tmp_path), "phase1")
            self.assertGreater(stats.parse_errors, 0)


if __name__ == "__main__":
    unittest.main()
