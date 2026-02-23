"""Phase 4 tests: query API contract stabilization and result metadata."""

from __future__ import annotations

import unittest

from graphrag.query import (
    calculate_blast_radius,
    get_entity_neighbors,
    get_inheritance_tree,
)


class _FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        if not self._records:
            return None
        return self._records[0]


class _FakeSession:
    def __init__(self, shared_responses: list[list[dict]], calls: list[dict]):
        self._shared_responses = shared_responses
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def run(self, query, **params):
        query_text = getattr(query, "text", str(query))
        query_timeout = getattr(query, "timeout", None)
        self._calls.append(
            {"query": query_text, "params": params, "timeout": query_timeout}
        )
        if not self._shared_responses:
            raise AssertionError("No queued fake response for run() call")
        return _FakeResult(self._shared_responses.pop(0))


class _FakeDriver:
    def __init__(self, responses: list[list[dict]]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def session(self):
        return _FakeSession(self._responses, self.calls)


class TestPhase4BlastRadiusContract(unittest.TestCase):
    def test_missing_root_returns_typed_status(self) -> None:
        driver = _FakeDriver(responses=[[]])
        result = calculate_blast_radius("repo::f.cpp::Function::run", driver)
        self.assertEqual(result.metadata.status, "missing_root")
        self.assertEqual(result.total_count, 0)

    def test_empty_result_returns_typed_status(self) -> None:
        driver = _FakeDriver(
            responses=[
                [{"uri": "repo::f.cpp::Function::run"}],  # root lookup
                [],  # blast query
            ]
        )
        result = calculate_blast_radius("repo::f.cpp::Function::run", driver)
        self.assertEqual(result.metadata.status, "empty_result")
        self.assertEqual(result.metadata.reason, "no_matching_paths")
        self.assertEqual(result.total_count, 0)

    def test_pagination_sets_next_cursor_and_parses_cursor(self) -> None:
        records = [
            {
                "uri": "repo::a.cpp::Function::A",
                "type": "Function",
                "name": "A",
                "file": "a.cpp",
                "depth": 1,
                "chain": ["CALLS"],
            },
            {
                "uri": "repo::b.cpp::Function::B",
                "type": "Function",
                "name": "B",
                "file": "b.cpp",
                "depth": 2,
                "chain": ["CALLS", "CALLS"],
            },
            {
                "uri": "repo::c.cpp::Function::C",
                "type": "Function",
                "name": "C",
                "file": "c.cpp",
                "depth": 3,
                "chain": ["CALLS", "CALLS", "CALLS"],
            },
        ]
        driver = _FakeDriver(
            responses=[
                [{"uri": "repo::f.cpp::Function::run"}],
                records,
            ]
        )
        first_page = calculate_blast_radius(
            "repo::f.cpp::Function::run",
            driver,
            max_results=2,
        )
        self.assertEqual(first_page.total_count, 2)
        self.assertEqual(first_page.metadata.next_cursor, "2|repo::b.cpp::Function::B")

        cursor_driver = _FakeDriver(
            responses=[
                [{"uri": "repo::f.cpp::Function::run"}],
                [],
            ]
        )
        calculate_blast_radius(
            "repo::f.cpp::Function::run",
            cursor_driver,
            cursor=first_page.metadata.next_cursor,
        )
        blast_call_params = cursor_driver.calls[1]["params"]
        self.assertEqual(blast_call_params["cursor_depth"], 2)
        self.assertEqual(blast_call_params["cursor_uri"], "repo::b.cpp::Function::B")

    def test_invalid_cursor_raises(self) -> None:
        driver = _FakeDriver(responses=[])
        with self.assertRaises(ValueError):
            calculate_blast_radius(
                "repo::f.cpp::Function::run",
                driver,
                cursor="bad-cursor",
            )

    def test_invalid_relationship_type_raises(self) -> None:
        driver = _FakeDriver(responses=[])
        with self.assertRaises(ValueError):
            calculate_blast_radius(
                "repo::f.cpp::Function::run",
                driver,
                relationship_types=["BAD_REL"],
            )


class TestPhase4NeighborContract(unittest.TestCase):
    def test_neighbors_default_to_entity_only(self) -> None:
        driver = _FakeDriver(
            responses=[
                [{"uri": "repo::f.cpp::Function::run"}],  # root
                [{"uri": "repo::a.cpp::Function::A", "type": "Function", "relationship": "CALLS"}],
                [{"uri": "repo::b.h::Class::B", "type": "Class", "relationship": "USES_TYPE"}],
            ]
        )
        result = get_entity_neighbors("repo::f.cpp::Function::run", driver)
        self.assertEqual(len(result["inbound"]), 1)
        self.assertEqual(len(result["outbound"]), 1)
        self.assertEqual(result["metadata"].status, "ok")
        self.assertIn("(src:Entity)-[r]->(tgt:Entity", driver.calls[1]["query"])
        self.assertIn("(src:Entity {global_uri: $uri})-[r]->(tgt:Entity)", driver.calls[2]["query"])

    def test_neighbors_can_include_non_entity_nodes(self) -> None:
        driver = _FakeDriver(
            responses=[
                [{"uri": "repo::f.cpp::Function::run"}],
                [],
                [],
            ]
        )
        get_entity_neighbors(
            "repo::f.cpp::Function::run",
            driver,
            include_non_entity=True,
        )
        self.assertIn("MATCH (src)-[r]->(tgt:Entity", driver.calls[1]["query"])
        self.assertIn("MATCH (src:Entity {global_uri: $uri})-[r]->(tgt)", driver.calls[2]["query"])

    def test_neighbors_missing_root_returns_status(self) -> None:
        driver = _FakeDriver(responses=[[]])
        result = get_entity_neighbors("repo::f.cpp::Function::run", driver)
        self.assertEqual(result["metadata"].status, "missing_root")
        self.assertEqual(result["inbound"], [])
        self.assertEqual(result["outbound"], [])


class TestPhase4InheritanceContract(unittest.TestCase):
    def test_inheritance_missing_root_returns_status(self) -> None:
        driver = _FakeDriver(responses=[[]])
        result = get_inheritance_tree("repo::x.h::Class::Node", driver)
        self.assertEqual(result["metadata"].status, "missing_root")
        self.assertEqual(result["ancestors"], [])
        self.assertEqual(result["descendants"], [])

    def test_inheritance_queries_use_deterministic_order(self) -> None:
        driver = _FakeDriver(
            responses=[
                [{"uri": "repo::x.h::Class::Node"}],  # root
                [{"uri": "repo::a.h::Class::Base"}],  # ancestors
                [{"uri": "repo::z.h::Class::Derived"}],  # descendants
            ]
        )
        result = get_inheritance_tree("repo::x.h::Class::Node", driver)
        self.assertEqual(result["metadata"].status, "ok")
        self.assertIn("ORDER BY depth ASC, uri ASC", driver.calls[1]["query"])
        self.assertIn("ORDER BY depth ASC, uri ASC", driver.calls[2]["query"])


if __name__ == "__main__":
    unittest.main()
