"""Reproducible integration tests using deterministic synthetic SCIP fixtures."""

from __future__ import annotations

import unittest

import pytest

from graphrag.neo4j_loader import (
    clear_repo_graph,
    get_neo4j_driver,
    ingest_graph,
    init_graph_schema,
)
from graphrag.query import calculate_blast_radius, get_entity_neighbors, get_inheritance_tree
from graphrag.scip_parser import parse_scip_index
from graphrag.tests.fixtures.build_scip_fixtures import BASIC_FIXTURE, build_all_fixtures

pytestmark = pytest.mark.integration


class TestFixtureGraphIntegration(unittest.TestCase):
    """Integration path: fixture SCIP -> Neo4j -> query APIs."""

    @classmethod
    def setUpClass(cls) -> None:
        build_all_fixtures(force=False)

        cls.repo_name = "phase5-fixture-integration"
        cls.index_path = str(BASIC_FIXTURE)

        try:
            cls.driver = get_neo4j_driver()
            init_graph_schema(cls.driver)
        except Exception as exc:
            raise unittest.SkipTest(f"Neo4j not available: {exc}")

        clear_repo_graph(cls.driver, cls.repo_name)
        cls.parse_result = parse_scip_index(cls.index_path, cls.repo_name)
        cls.stats = ingest_graph(cls.parse_result, cls.driver, cls.repo_name)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "driver"):
            clear_repo_graph(cls.driver, cls.repo_name)
            cls.driver.close()

    def test_fixture_ingestion_creates_entities_and_edges(self) -> None:
        with self.driver.session() as session:
            entity_count = session.run(
                """
                MATCH (e:Entity {repo_name: $repo})
                RETURN count(e) AS c
                """,
                repo=self.repo_name,
            ).single()["c"]
            edge_count = session.run(
                """
                MATCH (:Entity {repo_name: $repo})-[r]->(:Entity)
                RETURN count(r) AS c
                """,
                repo=self.repo_name,
            ).single()["c"]

        self.assertGreaterEqual(entity_count, 3)
        self.assertGreater(edge_count, 0)

    def test_fixture_neighbors_contract(self) -> None:
        with self.driver.session() as session:
            record = session.run(
                """
                MATCH (e:Entity {repo_name: $repo})
                WHERE EXISTS((e)-[]->()) OR EXISTS((e)<-[]-())
                RETURN e.global_uri AS uri
                LIMIT 1
                """,
                repo=self.repo_name,
            ).single()
            if record is None:
                self.skipTest("No entity with relationships found in fixture graph")

        result = get_entity_neighbors(record["uri"], self.driver, repo_name=self.repo_name)
        self.assertIn("metadata", result)
        self.assertIn(result["metadata"].status, {"ok", "empty_result"})
        for neighbor in result["inbound"] + result["outbound"]:
            self.assertIsNotNone(neighbor["uri"])
            self.assertIsNotNone(neighbor["type"])

    def test_fixture_blast_radius_query(self) -> None:
        with self.driver.session() as session:
            record = session.run(
                """
                MATCH (e:Entity {repo_name: $repo})-[r]->()
                RETURN e.global_uri AS uri
                LIMIT 1
                """,
                repo=self.repo_name,
            ).single()
            if record is None:
                self.skipTest("No source node with outbound edges")

        blast = calculate_blast_radius(
            record["uri"],
            self.driver,
            max_depth=2,
            direction="downstream",
            repo_name=self.repo_name,
            max_results=20,
        )
        self.assertIn(blast.metadata.status, {"ok", "empty_result"})
        self.assertLessEqual(blast.max_depth_reached, 2)

    def test_fixture_inheritance_query_empty_metadata(self) -> None:
        with self.driver.session() as session:
            record = session.run(
                """
                MATCH (e:Entity {repo_name: $repo})
                WHERE e.entity_type = 'Class'
                RETURN e.global_uri AS uri
                LIMIT 1
                """,
                repo=self.repo_name,
            ).single()
            if record is None:
                self.skipTest("No class entity in fixture graph")

        tree = get_inheritance_tree(record["uri"], self.driver, repo_name=self.repo_name)
        self.assertIn("metadata", tree)
        self.assertIn(tree["metadata"].status, {"ok", "empty_result"})
        self.assertIsInstance(tree["ancestors"], list)
        self.assertIsInstance(tree["descendants"], list)


if __name__ == "__main__":
    unittest.main()
