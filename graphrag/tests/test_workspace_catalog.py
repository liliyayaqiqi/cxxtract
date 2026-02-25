"""Tests for workspace symbol ownership catalog."""

import unittest

from graphrag.scip_parser import ScipParseResult, ScipReference, ScipSymbolDef
from graphrag.workspace_catalog import build_workspace_symbol_catalog


class TestWorkspaceSymbolCatalog(unittest.TestCase):
    def test_build_catalog_from_local_definitions(self) -> None:
        symbol = "cxx . . $ webrtc/RtpSender#"
        repo_parse_results = [
            (
                "repo-a",
                ScipParseResult(
                    symbols=[
                        ScipSymbolDef(
                            scip_symbol=symbol,
                            file_path="api/rtp_sender.h",
                            kind=0,
                            display_name="RtpSender",
                            is_local_definition=True,
                        )
                    ],
                    references=[],
                    document_count=1,
                    external_symbol_count=0,
                ),
            )
        ]

        catalog = build_workspace_symbol_catalog(repo_parse_results)
        self.assertEqual(catalog.symbol_owner_repo[symbol], "repo-a")
        self.assertEqual(
            catalog.symbol_owner_file[("repo-a", symbol)],
            "api/rtp_sender.h",
        )
        self.assertEqual(catalog.conflicts, [])

    def test_conflict_uses_stable_repo_order_without_override(self) -> None:
        symbol = "cxx . . $ common/Node#"
        repo_parse_results = [
            (
                "repo-a",
                ScipParseResult(
                    symbols=[
                        ScipSymbolDef(
                            scip_symbol=symbol,
                            file_path="a/node.h",
                            kind=0,
                            display_name="Node",
                            is_local_definition=True,
                        )
                    ],
                    references=[],
                    document_count=1,
                    external_symbol_count=0,
                ),
            ),
            (
                "repo-b",
                ScipParseResult(
                    symbols=[
                        ScipSymbolDef(
                            scip_symbol=symbol,
                            file_path="b/node.h",
                            kind=0,
                            display_name="Node",
                            is_local_definition=True,
                        )
                    ],
                    references=[],
                    document_count=1,
                    external_symbol_count=0,
                ),
            ),
        ]

        catalog = build_workspace_symbol_catalog(repo_parse_results)
        self.assertEqual(catalog.symbol_owner_repo[symbol], "repo-a")
        self.assertEqual(len(catalog.conflicts), 1)
        self.assertEqual(catalog.conflicts[0].reason, "stable_order")

    def test_conflict_honors_owner_override(self) -> None:
        symbol = "cxx . . $ common/Node#"
        repo_parse_results = [
            (
                "repo-a",
                ScipParseResult(
                    symbols=[
                        ScipSymbolDef(
                            scip_symbol=symbol,
                            file_path="a/node.h",
                            kind=0,
                            display_name="Node",
                            is_local_definition=True,
                        )
                    ],
                    references=[ScipReference(symbol, "a/node.cpp", None, "READ", 1)],
                    document_count=1,
                    external_symbol_count=0,
                ),
            ),
            (
                "repo-b",
                ScipParseResult(
                    symbols=[
                        ScipSymbolDef(
                            scip_symbol=symbol,
                            file_path="b/node.h",
                            kind=0,
                            display_name="Node",
                            is_local_definition=True,
                        )
                    ],
                    references=[],
                    document_count=1,
                    external_symbol_count=0,
                ),
            ),
        ]

        catalog = build_workspace_symbol_catalog(
            repo_parse_results,
            owner_overrides={symbol: "repo-b"},
        )
        self.assertEqual(catalog.symbol_owner_repo[symbol], "repo-b")
        self.assertEqual(catalog.conflicts[0].reason, "override")


if __name__ == "__main__":
    unittest.main()
