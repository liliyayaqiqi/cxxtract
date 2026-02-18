"""
Unit tests for parser.py

Tests tree-sitter parser initialization, byte parsing, and file parsing.
"""

import unittest
import os
from pathlib import Path
from extraction.parser import create_parser, parse_bytes, parse_file


class TestParserInitialization(unittest.TestCase):
    """Test parser creation and initialization."""
    
    def test_create_parser(self):
        """Test that create_parser returns a valid Parser instance."""
        parser = create_parser()
        self.assertIsNotNone(parser)
        # Verify it has a language set
        self.assertIsNotNone(parser.language)
    
    def test_parser_language(self):
        """Test that the parser is configured with C++ language."""
        parser = create_parser()
        # The language should be set
        self.assertIsNotNone(parser.language)


class TestParseBytes(unittest.TestCase):
    """Test parsing raw bytes of C++ code."""
    
    def test_parse_simple_function(self):
        """Test parsing a simple function definition."""
        source = b"int main() { return 0; }"
        tree = parse_bytes(source)
        
        self.assertIsNotNone(tree)
        self.assertEqual(tree.root_node.type, "translation_unit")
        self.assertFalse(tree.root_node.has_error)
    
    def test_parse_class(self):
        """Test parsing a class definition."""
        source = b"""
        class Foo {
        public:
            void bar();
        };
        """
        tree = parse_bytes(source)
        
        self.assertIsNotNone(tree)
        self.assertEqual(tree.root_node.type, "translation_unit")
        self.assertFalse(tree.root_node.has_error)
    
    def test_parse_empty(self):
        """Test parsing empty source."""
        source = b""
        tree = parse_bytes(source)
        
        self.assertIsNotNone(tree)
        self.assertEqual(tree.root_node.type, "translation_unit")
        # Empty file should have no children
        self.assertEqual(len(tree.root_node.children), 0)
    
    def test_parse_with_comments(self):
        """Test parsing code with comments."""
        source = b"""
        // This is a comment
        /* Multi-line
           comment */
        /// Doxygen comment
        void foo() {}
        """
        tree = parse_bytes(source)
        
        self.assertIsNotNone(tree)
        self.assertEqual(tree.root_node.type, "translation_unit")
    
    def test_parse_invalid_type(self):
        """Test that parse_bytes raises TypeError for non-bytes input."""
        with self.assertRaises(TypeError):
            parse_bytes("not bytes")  # Should be bytes, not str
    
    def test_parse_with_errors(self):
        """Test parsing code with syntax errors."""
        # Missing closing brace
        source = b"void broken() { int x = 10;"
        tree = parse_bytes(source)
        
        self.assertIsNotNone(tree)
        self.assertEqual(tree.root_node.type, "translation_unit")
        # Should have errors
        self.assertTrue(tree.root_node.has_error)


class TestParseFile(unittest.TestCase):
    """Test parsing C++ files from disk."""
    
    def setUp(self):
        """Set up test fixtures path."""
        self.fixtures_dir = Path(__file__).parent / "fixtures"
        self.assertTrue(self.fixtures_dir.exists(), 
                       f"Fixtures directory not found: {self.fixtures_dir}")
    
    def test_parse_simple_function_file(self):
        """Test parsing simple_function.cpp fixture."""
        file_path = self.fixtures_dir / "simple_function.cpp"
        tree, source_bytes = parse_file(str(file_path))
        
        self.assertIsNotNone(tree)
        self.assertIsNotNone(source_bytes)
        self.assertEqual(tree.root_node.type, "translation_unit")
        self.assertGreater(len(source_bytes), 0)
        self.assertFalse(tree.root_node.has_error)
    
    def test_parse_class_file(self):
        """Test parsing simple_class.h fixture."""
        file_path = self.fixtures_dir / "simple_class.h"
        tree, source_bytes = parse_file(str(file_path))
        
        self.assertIsNotNone(tree)
        self.assertEqual(tree.root_node.type, "translation_unit")
        self.assertFalse(tree.root_node.has_error)
    
    def test_parse_namespace_file(self):
        """Test parsing namespace_example.cpp fixture."""
        file_path = self.fixtures_dir / "namespace_example.cpp"
        tree, source_bytes = parse_file(str(file_path))
        
        self.assertIsNotNone(tree)
        self.assertEqual(tree.root_node.type, "translation_unit")
        self.assertFalse(tree.root_node.has_error)
    
    def test_parse_template_file(self):
        """Test parsing template_example.h fixture."""
        file_path = self.fixtures_dir / "template_example.h"
        tree, source_bytes = parse_file(str(file_path))
        
        self.assertIsNotNone(tree)
        self.assertEqual(tree.root_node.type, "translation_unit")
        # Templates should parse successfully
        self.assertFalse(tree.root_node.has_error)
    
    def test_parse_broken_syntax_file(self):
        """Test parsing file with syntax errors (error resilience)."""
        file_path = self.fixtures_dir / "broken_syntax.cpp"
        tree, source_bytes = parse_file(str(file_path))
        
        self.assertIsNotNone(tree)
        self.assertEqual(tree.root_node.type, "translation_unit")
        # Should have errors but still return a tree
        self.assertTrue(tree.root_node.has_error)
    
    def test_parse_nonexistent_file(self):
        """Test that parsing a nonexistent file raises FileNotFoundError."""
        file_path = self.fixtures_dir / "nonexistent.cpp"
        
        with self.assertRaises(FileNotFoundError):
            parse_file(str(file_path))
    
    def test_source_bytes_match_file(self):
        """Test that returned source bytes match the file content."""
        file_path = self.fixtures_dir / "simple_function.cpp"
        tree, source_bytes = parse_file(str(file_path))
        
        # Read file again and compare
        with open(file_path, "rb") as f:
            expected_bytes = f.read()
        
        self.assertEqual(source_bytes, expected_bytes)


class TestASTParsing(unittest.TestCase):
    """Test AST structure of parsed trees."""
    
    def test_function_node_exists(self):
        """Test that function definitions are parsed correctly."""
        source = b"void foo() {}"
        tree = parse_bytes(source)
        
        # Should have at least one child
        self.assertGreater(tree.root_node.child_count, 0)
        
        # Find function_definition node
        found_function = False
        for child in tree.root_node.children:
            if child.type == "function_definition":
                found_function = True
                break
        
        self.assertTrue(found_function, "function_definition node not found in AST")
    
    def test_class_node_exists(self):
        """Test that class definitions are parsed correctly."""
        source = b"class Foo {};"
        tree = parse_bytes(source)
        
        # Look for class_specifier in the tree
        found_class = False
        for child in tree.root_node.children:
            # Class can be wrapped in declaration
            if child.type == "declaration":
                type_child = child.child_by_field_name("type")
                if type_child and type_child.type == "class_specifier":
                    found_class = True
                    break
            elif child.type == "class_specifier":
                found_class = True
                break
        
        self.assertTrue(found_class, "class_specifier node not found in AST")
    
    def test_comment_node_exists(self):
        """Test that comments are captured in the AST."""
        source = b"// Comment\nvoid foo() {}"
        tree = parse_bytes(source)
        
        # Look for comment nodes
        found_comment = False
        for child in tree.root_node.children:
            if child.type == "comment":
                found_comment = True
                break
        
        self.assertTrue(found_comment, "comment node not found in AST")


if __name__ == "__main__":
    unittest.main()
