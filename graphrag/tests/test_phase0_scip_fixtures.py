"""Phase 0 tests: deterministic SCIP fixture parsing."""

import unittest
from pathlib import Path

from graphrag.scip_parser import parse_scip_index
from graphrag.tests.fixtures.build_scip_fixtures import (
    BASIC_FIXTURE,
    NESTED_FIXTURE,
    build_all_fixtures,
)


class TestScipFixtureParsingPhase0(unittest.TestCase):
    """Validate parser behavior against deterministic synthetic fixtures."""

    @classmethod
    def setUpClass(cls) -> None:
        build_all_fixtures(force=False)

    def test_basic_fixture_counts(self) -> None:
        """Basic fixture should parse into expected symbol/reference counts."""
        result = parse_scip_index(str(BASIC_FIXTURE), repo_name="phase0")
        self.assertEqual(result.document_count, 1)
        self.assertEqual(len(result.symbols), 3)
        self.assertEqual(len(result.references), 1)
        self.assertEqual(result.references[0].enclosing_symbol, "cxx . . $ YAML/run(89ab).")

    def test_basic_fixture_parse_is_deterministic(self) -> None:
        """Parsing the same .scip fixture multiple times should be stable."""
        result_1 = parse_scip_index(str(BASIC_FIXTURE), repo_name="phase0")
        result_2 = parse_scip_index(str(BASIC_FIXTURE), repo_name="phase0")

        symbols_1 = [(s.scip_symbol, s.file_path, s.kind, s.display_name) for s in result_1.symbols]
        symbols_2 = [(s.scip_symbol, s.file_path, s.kind, s.display_name) for s in result_2.symbols]
        refs_1 = [(r.scip_symbol, r.file_path, r.enclosing_symbol, r.role, r.line) for r in result_1.references]
        refs_2 = [(r.scip_symbol, r.file_path, r.enclosing_symbol, r.role, r.line) for r in result_2.references]

        self.assertEqual(symbols_1, symbols_2)
        self.assertEqual(refs_1, refs_2)

    def test_nested_scope_reference_should_bind_to_innermost_scope(self) -> None:
        """Reference in inner function should bind to innermost symbol."""
        result = parse_scip_index(str(NESTED_FIXTURE), repo_name="phase0")
        self.assertEqual(len(result.references), 1)
        self.assertEqual(result.references[0].enclosing_symbol, "cxx . . $ YAML/inner(bbbb).")


if __name__ == "__main__":
    unittest.main()
