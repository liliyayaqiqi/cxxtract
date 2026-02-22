"""
Integration tests for the complete GraphRAG pipeline.

Tests the full workflow: SCIP parsing -> Neo4j ingestion -> Blast radius queries.
Requires yaml-cpp SCIP index and Neo4j running.
"""

import unittest
import os

from graphrag.scip_parser import parse_scip_index
from graphrag.neo4j_loader import (
    get_neo4j_driver,
    init_graph_schema,
    clear_repo_graph,
    ingest_graph,
)
from graphrag.query import (
    calculate_blast_radius,
    get_entity_neighbors,
    get_inheritance_tree,
)


class TestEndToEndPipeline(unittest.TestCase):
    """Test complete pipeline from SCIP to Neo4j."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test database once for all tests."""
        # Check if yaml-cpp index exists
        cls.index_path = "/Users/yaqi.li/testproject/yaml-cpp-master/index.scip"
        if not os.path.isfile(cls.index_path):
            raise unittest.SkipTest("yaml-cpp index.scip not available")
        
        try:
            cls.driver = get_neo4j_driver()
            init_graph_schema(cls.driver)
        except Exception:
            raise unittest.SkipTest("Neo4j not available")
        
        # Clear and ingest test data
        cls.repo_name = "yaml-cpp-test"
        clear_repo_graph(cls.driver, cls.repo_name)
        
        print("\nParsing SCIP index...")
        cls.parse_result = parse_scip_index(cls.index_path, cls.repo_name)
        
        print(f"Ingesting {len(cls.parse_result.symbols)} symbols...")
        cls.stats = ingest_graph(cls.parse_result, cls.driver, cls.repo_name)
        print(f"Ingestion stats: {cls.stats}\n")
    
    @classmethod
    def tearDownClass(cls):
        """Clean up after all tests."""
        if hasattr(cls, "driver"):
            clear_repo_graph(cls.driver, cls.repo_name)
            cls.driver.close()
    
    def test_nodes_created(self):
        """Test that nodes were created in Neo4j."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (e:Entity {repo_name: $repo})
                RETURN count(e) AS total
                """,
                repo=self.repo_name,
            )
            total = result.single()["total"]
            
            self.assertGreater(total, 1000)  # Expect significant node count
            # Note: Some nodes may fail to insert due to external symbols
            # with no file_path, so total may be less than stats.nodes_created
    
    def test_inheritance_edges(self):
        """Test that INHERITS edges were created."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (:Entity {repo_name: $repo})-[r:INHERITS]->()
                RETURN count(r) AS count
                """,
                repo=self.repo_name,
            )
            count = result.single()["count"]
            
            self.assertGreater(count, 0)
    
    def test_calls_edges(self):
        """Test that CALLS edges were created."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (:Entity {repo_name: $repo})-[r:CALLS]->()
                RETURN count(r) AS count
                """,
                repo=self.repo_name,
            )
            count = result.single()["count"]
            
            self.assertGreater(count, 0)
    
    def test_blast_radius_upstream(self):
        """Test upstream blast radius calculation."""
        # Find a class with dependents
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (e:Entity {repo_name: $repo})<-[r]-(dependent)
                WHERE e.entity_type = 'Class'
                RETURN e.global_uri AS uri, count(r) AS deps
                ORDER BY deps DESC
                LIMIT 1
                """,
                repo=self.repo_name,
            )
            
            rec = result.single()
            if not rec:
                self.skipTest("No suitable entity found")
            
            test_uri = rec["uri"]
        
        # Calculate blast radius
        blast = calculate_blast_radius(
            test_uri,
            self.driver,
            max_depth=3,
            direction="upstream",
        )
        
        self.assertGreater(blast.total_count, 0)
        self.assertLessEqual(blast.max_depth_reached, 3)
        
        # Verify structure
        for entity in blast.affected_entities:
            self.assertIsNotNone(entity.global_uri)
            self.assertIsNotNone(entity.entity_type)
            self.assertGreater(entity.depth, 0)
    
    def test_blast_radius_downstream(self):
        """Test downstream blast radius calculation."""
        # Find a class with dependencies
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (e:Entity {repo_name: $repo})-[r]->(dependency)
                RETURN e.global_uri AS uri, count(r) AS deps
                ORDER BY deps DESC
                LIMIT 1
                """,
                repo=self.repo_name,
            )
            
            rec = result.single()
            if not rec:
                self.skipTest("No suitable entity found")
            
            test_uri = rec["uri"]
        
        blast = calculate_blast_radius(
            test_uri,
            self.driver,
            max_depth=2,
            direction="downstream",
        )
        
        self.assertGreater(blast.total_count, 0)
    
    def test_get_entity_neighbors(self):
        """Test getting immediate neighbors of an entity."""
        # Find any entity with relationships
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (e:Entity {repo_name: $repo})
                WHERE EXISTS((e)-[]->()) OR EXISTS((e)<-[]-())
                RETURN e.global_uri AS uri
                LIMIT 1
                """,
                repo=self.repo_name,
            )
            
            rec = result.single()
            if not rec:
                self.skipTest("No entity with relationships found")
            
            test_uri = rec["uri"]
        
        neighbors = get_entity_neighbors(test_uri, self.driver)
        
        self.assertIn("inbound", neighbors)
        self.assertIn("outbound", neighbors)
        self.assertIsInstance(neighbors["inbound"], list)
        self.assertIsInstance(neighbors["outbound"], list)
    
    def test_get_inheritance_tree(self):
        """Test getting full inheritance hierarchy."""
        # Find a class that inherits from something
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (child:Entity {repo_name: $repo})-[:INHERITS]->(parent)
                RETURN child.global_uri AS uri
                LIMIT 1
                """,
                repo=self.repo_name,
            )
            
            rec = result.single()
            if not rec:
                self.skipTest("No inheritance found")
            
            test_uri = rec["uri"]
        
        tree = get_inheritance_tree(test_uri, self.driver)
        
        self.assertIn("ancestors", tree)
        self.assertIn("descendants", tree)
        self.assertGreater(len(tree["ancestors"]), 0)
    
    def test_cross_file_edges_have_correct_target_uri(self):
        """Test that edges between symbols in different files use the target's
        actual definition file, not the source's file.
        
        This is a regression test for the bug where relationship and reference
        edge targets were constructed with the source symbol's file_path
        instead of the target's Document.relative_path.
        """
        with self.driver.session() as session:
            # Find an INHERITS edge where child and parent are in different files
            result = session.run(
                """
                MATCH (child:Entity {repo_name: $repo})-[:INHERITS]->(parent)
                WHERE child.file_path <> parent.file_path
                RETURN child.global_uri AS child_uri,
                       child.file_path AS child_file,
                       parent.global_uri AS parent_uri,
                       parent.file_path AS parent_file
                LIMIT 5
                """,
                repo=self.repo_name,
            )
            
            records = list(result)
            
            if not records:
                self.skipTest("No cross-file inheritance found")
            
            for rec in records:
                child_uri = rec["child_uri"]
                parent_uri = rec["parent_uri"]
                child_file = rec["child_file"]
                parent_file = rec["parent_file"]
                
                # The child and parent MUST be in different files
                self.assertNotEqual(child_file, parent_file)
                
                # The parent's URI must contain the parent's actual file path,
                # NOT the child's file path
                self.assertIn(
                    parent_file,
                    parent_uri,
                    f"Parent URI {parent_uri} does not contain parent's "
                    f"file path {parent_file}",
                )
                self.assertNotIn(
                    child_file,
                    parent_uri,
                    f"Parent URI {parent_uri} incorrectly contains child's "
                    f"file path {child_file} instead of {parent_file}",
                )
    
    def test_cross_file_calls_edge_targets(self):
        """Test that CALLS edges across files use the callee's definition file."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (caller:Entity {repo_name: $repo})-[:CALLS]->(callee)
                WHERE caller.file_path <> callee.file_path
                RETURN callee.global_uri AS callee_uri,
                       callee.file_path AS callee_file
                LIMIT 5
                """,
                repo=self.repo_name,
            )
            
            records = list(result)
            
            if not records:
                self.skipTest("No cross-file CALLS found")
            
            for rec in records:
                callee_uri = rec["callee_uri"]
                callee_file = rec["callee_file"]
                
                # The callee's URI must contain the callee's file path
                self.assertIn(
                    callee_file,
                    callee_uri,
                    f"Callee URI {callee_uri} does not contain callee's "
                    f"file path {callee_file}",
                )


if __name__ == "__main__":
    unittest.main()
