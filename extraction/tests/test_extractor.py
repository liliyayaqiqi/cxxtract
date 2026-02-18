"""
Integration tests for extractor.py

Tests the high-level orchestration functions.
"""

import unittest
import os
import tempfile
from pathlib import Path
from extraction.extractor import (
    extract_file,
    extract_directory,
    discover_cpp_files,
    extract_to_dict_list,
    ExtractionStats,
)


class TestExtractionStats(unittest.TestCase):
    """Test ExtractionStats class."""
    
    def test_creation(self):
        """Test creating stats object."""
        stats = ExtractionStats()
        self.assertEqual(stats.files_processed, 0)
        self.assertEqual(stats.files_failed, 0)
        self.assertEqual(stats.entities_extracted, 0)
        self.assertEqual(stats.parse_errors, 0)
    
    def test_to_dict(self):
        """Test converting stats to dictionary."""
        stats = ExtractionStats()
        stats.files_processed = 5
        stats.entities_extracted = 20
        
        result = stats.to_dict()
        self.assertEqual(result["files_processed"], 5)
        self.assertEqual(result["entities_extracted"], 20)
    
    def test_str_representation(self):
        """Test string representation."""
        stats = ExtractionStats()
        stats.files_processed = 3
        
        s = str(stats)
        self.assertIn("processed=3", s)


class TestExtractFile(unittest.TestCase):
    """Test extracting from a single file."""
    
    def setUp(self):
        """Set up test fixtures path."""
        self.fixtures_dir = Path(__file__).parent / "fixtures"
    
    def test_extract_simple_function_file(self):
        """Test extracting from simple_function.cpp."""
        file_path = self.fixtures_dir / "simple_function.cpp"
        entities = extract_file(str(file_path), "test_repo")
        
        self.assertGreater(len(entities), 0)
        # Should have the 'add' function
        entity_names = [e.entity_name for e in entities]
        self.assertIn("add", entity_names)
    
    def test_extract_with_repo_root(self):
        """Test extraction with explicit repo root."""
        file_path = self.fixtures_dir / "simple_function.cpp"
        repo_root = self.fixtures_dir.parent.parent.parent  # testproject_opencode
        
        entities = extract_file(
            str(file_path),
            "test_repo",
            str(repo_root)
        )
        
        self.assertGreater(len(entities), 0)
        # Check that relative path is computed correctly
        for entity in entities:
            self.assertIn("extraction/tests/fixtures", entity.file_path)
    
    def test_extract_nonexistent_file(self):
        """Test that nonexistent file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            extract_file("/nonexistent/file.cpp", "test_repo")
    
    def test_extract_non_cpp_file(self):
        """Test that non-C++ file raises ValueError."""
        # Create a temporary non-C++ file
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            temp_path = f.name
        
        try:
            with self.assertRaises(ValueError):
                extract_file(temp_path, "test_repo")
        finally:
            os.unlink(temp_path)
    
    def test_global_uri_format(self):
        """Test that global URIs are correctly formatted."""
        file_path = self.fixtures_dir / "simple_function.cpp"
        entities = extract_file(str(file_path), "my_repo", str(self.fixtures_dir))
        
        for entity in entities:
            # Should start with repo name
            self.assertTrue(entity.global_uri.startswith("my_repo::"))
            # Should contain entity type
            self.assertIn(f"::{entity.entity_type}::", entity.global_uri)
            # Should end with entity name
            self.assertTrue(entity.global_uri.endswith(f"::{entity.entity_name}"))


class TestDiscoverCppFiles(unittest.TestCase):
    """Test C++ file discovery."""
    
    def setUp(self):
        """Set up test repo path."""
        self.test_repo = Path(__file__).parent / "fixtures" / "test_repo"
    
    def test_discover_files(self):
        """Test discovering C++ files in test repo."""
        if not self.test_repo.exists():
            self.skipTest(f"Test repo not found: {self.test_repo}")
        
        files = discover_cpp_files(str(self.test_repo))
        
        # Should find at least the files we created
        self.assertGreater(len(files), 0)
        
        # All should be absolute paths
        for f in files:
            self.assertTrue(os.path.isabs(f))
        
        # All should have C++ extensions
        for f in files:
            ext = os.path.splitext(f)[1]
            self.assertIn(ext, {'.cpp', '.cc', '.cxx', '.h', '.hpp', '.hxx', '.c'})
    
    def test_discover_excludes_hidden_dirs(self):
        """Test that hidden directories are excluded."""
        # Create a temporary directory with hidden subdirs
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create visible directory with C++ file
            os.makedirs(os.path.join(tmpdir, "src"))
            with open(os.path.join(tmpdir, "src", "test.cpp"), "w") as f:
                f.write("void foo() {}")
            
            # Create hidden directory with C++ file
            os.makedirs(os.path.join(tmpdir, ".hidden"))
            with open(os.path.join(tmpdir, ".hidden", "test.cpp"), "w") as f:
                f.write("void bar() {}")
            
            files = discover_cpp_files(tmpdir)
            
            # Should only find the visible file
            self.assertEqual(len(files), 1)
            self.assertIn("src", files[0])
            self.assertNotIn(".hidden", files[0])


class TestExtractDirectory(unittest.TestCase):
    """Test extracting from a directory tree."""
    
    def setUp(self):
        """Set up test repo path."""
        self.test_repo = Path(__file__).parent / "fixtures" / "test_repo"
    
    def test_extract_directory(self):
        """Test extracting from test_repo directory."""
        if not self.test_repo.exists():
            self.skipTest(f"Test repo not found: {self.test_repo}")
        
        entities, stats = extract_directory(str(self.test_repo), "test_project")
        
        # Should have processed some files
        self.assertGreater(stats.files_processed, 0)
        
        # Should have extracted some entities
        self.assertGreater(len(entities), 0)
        self.assertEqual(stats.entities_extracted, len(entities))
        
        # All entities should have the same repo name
        for entity in entities:
            self.assertEqual(entity.repo_name, "test_project")
    
    def test_extract_directory_with_stats(self):
        """Test that stats are correctly populated."""
        if not self.test_repo.exists():
            self.skipTest(f"Test repo not found: {self.test_repo}")
        
        entities, stats = extract_directory(str(self.test_repo), "test_project")
        
        # Verify stats
        self.assertIsInstance(stats, ExtractionStats)
        self.assertGreater(stats.files_processed, 0)
        self.assertEqual(stats.entities_extracted, len(entities))
    
    def test_extract_nonexistent_directory(self):
        """Test that nonexistent directory raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            extract_directory("/nonexistent/directory", "test_repo")
    
    def test_extract_empty_directory(self):
        """Test extracting from empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entities, stats = extract_directory(tmpdir, "empty_repo")
            
            self.assertEqual(len(entities), 0)
            self.assertEqual(stats.files_processed, 0)
            self.assertEqual(stats.entities_extracted, 0)
    
    def test_continue_on_error(self):
        """Test that extraction continues on error when flag is set."""
        # Create a temp directory with a valid and invalid file
        with tempfile.TemporaryDirectory() as tmpdir:
            # Valid C++ file
            valid_file = os.path.join(tmpdir, "valid.cpp")
            with open(valid_file, "w") as f:
                f.write("void foo() {}")
            
            # File with syntax errors (should still parse)
            error_file = os.path.join(tmpdir, "error.cpp")
            with open(error_file, "w") as f:
                f.write("void broken() { // missing closing brace")
            
            # Should continue despite errors
            entities, stats = extract_directory(tmpdir, "test", continue_on_error=True)
            
            # Both files should be processed
            self.assertEqual(stats.files_processed, 2)


class TestExtractToDictList(unittest.TestCase):
    """Test the convenience function extract_to_dict_list."""
    
    def setUp(self):
        """Set up test fixtures path."""
        self.fixtures_dir = Path(__file__).parent / "fixtures"
    
    def test_extract_file_to_dict(self):
        """Test extracting a file to dict list."""
        file_path = self.fixtures_dir / "simple_function.cpp"
        result = extract_to_dict_list(str(file_path), "test_repo")
        
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        
        # All items should be dictionaries
        for item in result:
            self.assertIsInstance(item, dict)
            self.assertIn("global_uri", item)
            self.assertIn("entity_type", item)
            self.assertIn("entity_name", item)
    
    def test_extract_directory_to_dict(self):
        """Test extracting a directory to dict list."""
        test_repo = self.fixtures_dir / "test_repo"
        
        if not test_repo.exists():
            self.skipTest(f"Test repo not found: {test_repo}")
        
        result = extract_to_dict_list(str(test_repo), "test_project")
        
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
    
    def test_dict_serialization(self):
        """Test that dicts can be JSON serialized."""
        import json
        
        file_path = self.fixtures_dir / "simple_function.cpp"
        result = extract_to_dict_list(str(file_path), "test_repo")
        
        # Should be JSON serializable
        json_str = json.dumps(result)
        self.assertIsInstance(json_str, str)
        
        # Should be able to load it back
        loaded = json.loads(json_str)
        self.assertEqual(len(loaded), len(result))


class TestEndToEndIntegration(unittest.TestCase):
    """End-to-end integration tests."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.fixtures_dir = Path(__file__).parent / "fixtures"
    
    def test_complete_extraction_pipeline(self):
        """Test the complete extraction pipeline from file to dict."""
        file_path = self.fixtures_dir / "qualified_names.cpp"
        
        # Extract entities
        entities = extract_file(str(file_path), "integration_test", str(self.fixtures_dir))
        
        # Verify we got entities
        self.assertGreater(len(entities), 0)
        
        # Verify namespace qualification works
        entity_names = [e.entity_name for e in entities]
        self.assertIn("outer::outer_function", entity_names)
        self.assertIn("outer::inner::inner_function", entity_names)
        
        # Convert to dicts
        dicts = [e.to_dict() for e in entities]
        
        # Verify dict structure
        for d in dicts:
            self.assertIn("global_uri", d)
            self.assertIn("code_text", d)
            self.assertIn("start_line", d)
            self.assertIn("end_line", d)
            
            # Verify URI format
            # Format: RepoName::FilePath::EntityType::EntityName (which may include ::)
            self.assertTrue(d["global_uri"].startswith("integration_test::"))
            self.assertIn(f"::{d['entity_type']}::", d["global_uri"])
            # The entity name is the last component(s) after EntityType
            uri_after_type = d["global_uri"].split(f"::{d['entity_type']}::")[1]
            self.assertEqual(uri_after_type, d["entity_name"])
    
    def test_template_extraction_pipeline(self):
        """Test extraction pipeline with templates."""
        file_path = self.fixtures_dir / "template_entities.cpp"
        
        entities = extract_file(str(file_path), "template_test", str(self.fixtures_dir))
        
        # Find templated entities
        templated = [e for e in entities if e.is_templated]
        non_templated = [e for e in entities if not e.is_templated]
        
        self.assertGreater(len(templated), 0, "Should have templated entities")
        self.assertGreater(len(non_templated), 0, "Should have non-templated entities")
        
        # Verify code text includes template prefix
        for entity in templated:
            self.assertIn("template", entity.code_text.lower())


if __name__ == "__main__":
    unittest.main()
