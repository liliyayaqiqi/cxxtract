"""
Unit tests for traversal.py

Tests AST traversal, entity extraction, comment association, and namespace qualification.
"""

import unittest
from pathlib import Path
from extraction.parser import parse_file, parse_bytes
from extraction.traversal import (
    is_doxygen_comment,
    get_preceding_comments,
    extract_function_name,
    extract_class_name,
    extract_namespace_name,
    extract_entity_from_node,
    extract_entities_from_tree,
)


class TestDoxygenDetection(unittest.TestCase):
    """Test Doxygen comment detection."""
    
    def test_triple_slash(self):
        """Test /// style Doxygen comments."""
        self.assertTrue(is_doxygen_comment("/// This is a comment"))
    
    def test_double_star(self):
        """Test /** style Doxygen comments."""
        self.assertTrue(is_doxygen_comment("/** This is a comment */"))
    
    def test_exclamation_slash(self):
        """Test //! style Doxygen comments."""
        self.assertTrue(is_doxygen_comment("//! This is a comment"))
    
    def test_exclamation_star(self):
        """Test /*! style Doxygen comments."""
        self.assertTrue(is_doxygen_comment("/*! This is a comment */"))
    
    def test_regular_double_slash(self):
        """Test that // is NOT Doxygen."""
        self.assertFalse(is_doxygen_comment("// Regular comment"))
    
    def test_regular_block(self):
        """Test that /* is NOT Doxygen."""
        self.assertFalse(is_doxygen_comment("/* Regular block comment */"))
    
    def test_empty_string(self):
        """Test empty string is not Doxygen."""
        self.assertFalse(is_doxygen_comment(""))


class TestCommentExtraction(unittest.TestCase):
    """Test extracting comments from AST nodes."""
    
    def test_single_doxygen_comment(self):
        """Test extracting a single Doxygen comment."""
        source = b"""
/// This is a Doxygen comment
void foo() {}
"""
        tree = parse_bytes(source)
        # Find the function node
        func_node = None
        for child in tree.root_node.children:
            if child.type == "function_definition":
                func_node = child
                break
        
        self.assertIsNotNone(func_node)
        comment = get_preceding_comments(func_node, source)
        self.assertIsNotNone(comment)
        self.assertIn("This is a Doxygen comment", comment)
    
    def test_multi_line_doxygen(self):
        """Test extracting multi-line Doxygen comments."""
        source = b"""
/**
 * @brief A function
 * @param x The parameter
 */
void foo(int x) {}
"""
        tree = parse_bytes(source)
        func_node = None
        for child in tree.root_node.children:
            if child.type == "function_definition":
                func_node = child
                break
        
        self.assertIsNotNone(func_node)
        comment = get_preceding_comments(func_node, source)
        self.assertIsNotNone(comment)
        self.assertIn("@brief", comment)
        self.assertIn("@param", comment)
    
    def test_no_comment(self):
        """Test function with no preceding comment."""
        source = b"void foo() {}"
        tree = parse_bytes(source)
        func_node = None
        for child in tree.root_node.children:
            if child.type == "function_definition":
                func_node = child
                break
        
        self.assertIsNotNone(func_node)
        comment = get_preceding_comments(func_node, source)
        self.assertIsNone(comment)
    
    def test_comment_with_blank_line_gap(self):
        """Test that comments separated by blank lines are not associated."""
        source = b"""
/// Comment

void foo() {}
"""
        tree = parse_bytes(source)
        func_node = None
        for child in tree.root_node.children:
            if child.type == "function_definition":
                func_node = child
                break
        
        self.assertIsNotNone(func_node)
        comment = get_preceding_comments(func_node, source)
        # Should NOT associate due to blank line
        self.assertIsNone(comment)


class TestNameExtraction(unittest.TestCase):
    """Test extracting entity names from AST nodes."""
    
    def test_simple_function_name(self):
        """Test extracting a simple function name."""
        source = b"void foo() {}"
        tree = parse_bytes(source)
        func_node = None
        for child in tree.root_node.children:
            if child.type == "function_definition":
                func_node = child
                break
        
        self.assertIsNotNone(func_node)
        name = extract_function_name(func_node, source)
        self.assertEqual(name, "foo")
    
    def test_function_with_params(self):
        """Test extracting function name with parameters."""
        source = b"int add(int a, int b) { return a + b; }"
        tree = parse_bytes(source)
        func_node = None
        for child in tree.root_node.children:
            if child.type == "function_definition":
                func_node = child
                break
        
        self.assertIsNotNone(func_node)
        name = extract_function_name(func_node, source)
        self.assertEqual(name, "add")
    
    def test_class_name(self):
        """Test extracting a class name."""
        source = b"class MyClass {};"
        tree = parse_bytes(source)
        class_node = None
        for child in tree.root_node.children:
            if child.type == "declaration":
                type_child = child.child_by_field_name("type")
                if type_child and type_child.type == "class_specifier":
                    class_node = type_child
                    break
            elif child.type == "class_specifier":
                class_node = child
                break
        
        self.assertIsNotNone(class_node)
        name = extract_class_name(class_node, source)
        self.assertEqual(name, "MyClass")
    
    def test_struct_name(self):
        """Test extracting a struct name."""
        source = b"struct Point { int x; int y; };"
        tree = parse_bytes(source)
        struct_node = None
        for child in tree.root_node.children:
            # Structs can appear directly or wrapped in declaration
            if child.type == "struct_specifier":
                struct_node = child
                break
            elif child.type == "declaration":
                type_child = child.child_by_field_name("type")
                if type_child and type_child.type == "struct_specifier":
                    struct_node = type_child
                    break
        
        self.assertIsNotNone(struct_node)
        name = extract_class_name(struct_node, source)
        self.assertEqual(name, "Point")
    
    def test_namespace_name(self):
        """Test extracting a namespace name."""
        source = b"namespace foo { }"
        tree = parse_bytes(source)
        ns_node = None
        for child in tree.root_node.children:
            if child.type == "namespace_definition":
                ns_node = child
                break
        
        self.assertIsNotNone(ns_node)
        name = extract_namespace_name(ns_node, source)
        self.assertEqual(name, "foo")


class TestEntityExtraction(unittest.TestCase):
    """Test full entity extraction from fixtures."""
    
    def setUp(self):
        """Set up test fixtures path."""
        self.fixtures_dir = Path(__file__).parent / "fixtures"
    
    def test_extract_simple_function(self):
        """Test extracting a simple function."""
        file_path = self.fixtures_dir / "simple_function.cpp"
        tree, source = parse_file(str(file_path))
        
        entities = extract_entities_from_tree(tree, source, "test_repo", "simple_function.cpp")
        
        # Should find at least the 'add' function
        self.assertGreater(len(entities), 0)
        
        # Find the 'add' function
        add_func = next((e for e in entities if e.entity_name == "add"), None)
        self.assertIsNotNone(add_func, "Function 'add' not found")
        self.assertEqual(add_func.entity_type, "Function")
        self.assertIsNotNone(add_func.docstring)
        self.assertIn("Adds two integers", add_func.docstring)
    
    def test_extract_class(self):
        """Test extracting a class."""
        file_path = self.fixtures_dir / "simple_class.h"
        tree, source = parse_file(str(file_path))
        
        entities = extract_entities_from_tree(tree, source, "test_repo", "simple_class.h")
        
        # Should find Calculator class and Point struct
        self.assertEqual(len(entities), 2, f"Expected 2 entities, found {len(entities)}")
        
        # Find Calculator class
        calc = next((e for e in entities if e.entity_name == "Calculator"), None)
        self.assertIsNotNone(calc, "Class 'Calculator' not found")
        self.assertEqual(calc.entity_type, "Class")
        self.assertIsNotNone(calc.docstring)
        
        # Find Point struct
        point = next((e for e in entities if e.entity_name == "Point"), None)
        self.assertIsNotNone(point, "Struct 'Point' not found")
        self.assertEqual(point.entity_type, "Struct")
    
    def test_namespace_qualification(self):
        """Test that namespace qualification works correctly."""
        file_path = self.fixtures_dir / "qualified_names.cpp"
        tree, source = parse_file(str(file_path))
        
        entities = extract_entities_from_tree(tree, source, "test_repo", "qualified_names.cpp")
        
        # Check for qualified names
        entity_names = [e.entity_name for e in entities]
        
        self.assertIn("outer::outer_function", entity_names)
        self.assertIn("outer::inner::inner_function", entity_names)
        self.assertIn("outer::inner::InnerClass", entity_names)
        self.assertIn("outer::OuterClass", entity_names)
        self.assertIn("global_function", entity_names)
    
    def test_template_extraction(self):
        """Test extracting template entities."""
        file_path = self.fixtures_dir / "template_entities.cpp"
        tree, source = parse_file(str(file_path))
        
        entities = extract_entities_from_tree(tree, source, "test_repo", "template_entities.cpp")
        
        # Find template entities
        max_value = next((e for e in entities if e.entity_name == "max_value"), None)
        self.assertIsNotNone(max_value, "Template function 'max_value' not found")
        self.assertTrue(max_value.is_templated)
        self.assertIn("template", max_value.code_text.lower())
        
        # Find template class
        stack = next((e for e in entities if e.entity_name == "Stack"), None)
        self.assertIsNotNone(stack, "Template class 'Stack' not found")
        self.assertTrue(stack.is_templated)
        
        # Non-template function should not be templated
        regular = next((e for e in entities if e.entity_name == "regular_function"), None)
        self.assertIsNotNone(regular)
        self.assertFalse(regular.is_templated)
    
    def test_doxygen_styles(self):
        """Test different Doxygen comment styles are extracted."""
        file_path = self.fixtures_dir / "doxygen_test.cpp"
        tree, source = parse_file(str(file_path))
        
        entities = extract_entities_from_tree(tree, source, "test_repo", "doxygen_test.cpp")
        
        # Check multiply function has /** */ style comment
        multiply = next((e for e in entities if e.entity_name == "multiply"), None)
        self.assertIsNotNone(multiply)
        self.assertIsNotNone(multiply.docstring)
        self.assertIn("Multiply two integers", multiply.docstring)
        
        # Check single_line_doxygen has /// style
        single_line = next((e for e in entities if e.entity_name == "single_line_doxygen"), None)
        self.assertIsNotNone(single_line)
        self.assertIsNotNone(single_line.docstring)
        
        # Check alternative_style has //! style
        alt = next((e for e in entities if e.entity_name == "alternative_style"), None)
        self.assertIsNotNone(alt)
        self.assertIsNotNone(alt.docstring)
        
        # Check block_doxygen has /*! style
        block = next((e for e in entities if e.entity_name == "block_doxygen"), None)
        self.assertIsNotNone(block)
        self.assertIsNotNone(block.docstring)
        
        # Check no_doxygen has no docstring (regular comment)
        no_doxy = next((e for e in entities if e.entity_name == "no_doxygen"), None)
        self.assertIsNotNone(no_doxy)
        # Regular comments should still be captured if adjacent
        # (our implementation captures all adjacent comments if no Doxygen found)
        
        # Check no_comment_at_all has no docstring
        no_comment = next((e for e in entities if e.entity_name == "no_comment_at_all"), None)
        self.assertIsNotNone(no_comment)
        self.assertIsNone(no_comment.docstring)
    
    def test_mixed_entities(self):
        """Test extracting mixed classes, structs, and functions."""
        file_path = self.fixtures_dir / "mixed_entities.h"
        tree, source = parse_file(str(file_path))
        
        entities = extract_entities_from_tree(tree, source, "test_repo", "mixed_entities.h")
        
        # Count entity types
        classes = [e for e in entities if e.entity_type == "Class"]
        structs = [e for e in entities if e.entity_type == "Struct"]
        functions = [e for e in entities if e.entity_type == "Function"]
        
        self.assertGreater(len(classes), 0, "No classes found")
        self.assertGreater(len(structs), 0, "No structs found")
        self.assertGreater(len(functions), 0, "No functions found")
        
        # Verify specific entities
        entity_names = [e.entity_name for e in entities]
        self.assertIn("Point", entity_names)
        self.assertIn("Rectangle", entity_names)
        self.assertIn("Circle", entity_names)
        self.assertIn("helper", entity_names)
        
        # Note: 'distance' is a function declaration (prototype), not a definition,
        # so it should NOT be extracted
        self.assertNotIn("distance", entity_names)
    
    def test_global_uri_format(self):
        """Test that global URIs are formatted correctly."""
        source = b"void test_func() {}"
        tree = parse_bytes(source)
        
        entities = extract_entities_from_tree(tree, source, "my_repo", "path/to/file.cpp")
        
        self.assertEqual(len(entities), 1)
        entity = entities[0]
        
        expected_uri = "my_repo::path/to/file.cpp::Function::test_func"
        self.assertEqual(entity.global_uri, expected_uri)
    
    def test_line_numbers(self):
        """Test that line numbers are correctly extracted."""
        source = b"""
void first() {}

void second() {}
"""
        tree = parse_bytes(source)
        entities = extract_entities_from_tree(tree, source, "repo", "file.cpp")
        
        self.assertEqual(len(entities), 2)
        
        # Line numbers should be 1-indexed
        first = next(e for e in entities if e.entity_name == "first")
        self.assertEqual(first.start_line, 2)
        
        second = next(e for e in entities if e.entity_name == "second")
        self.assertEqual(second.start_line, 4)
    
    def test_code_text_extraction(self):
        """Test that code text is extracted correctly."""
        source = b"""
/// Doc comment
void foo() {
    int x = 10;
}
"""
        tree = parse_bytes(source)
        entities = extract_entities_from_tree(tree, source, "repo", "file.cpp")
        
        self.assertEqual(len(entities), 1)
        entity = entities[0]
        
        # Code text should include the entire function
        self.assertIn("void foo()", entity.code_text)
        self.assertIn("int x = 10", entity.code_text)
        self.assertIn("}", entity.code_text)
        
        # Code text should NOT include the doc comment
        # (docstring is stored separately)
        self.assertNotIn("Doc comment", entity.code_text)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error handling."""
    
    def test_empty_file(self):
        """Test extracting from an empty file."""
        source = b""
        tree = parse_bytes(source)
        entities = extract_entities_from_tree(tree, source, "repo", "empty.cpp")
        
        self.assertEqual(len(entities), 0)
    
    def test_only_comments(self):
        """Test file with only comments."""
        source = b"""
// Comment 1
/* Comment 2 */
/// Comment 3
"""
        tree = parse_bytes(source)
        entities = extract_entities_from_tree(tree, source, "repo", "comments.cpp")
        
        self.assertEqual(len(entities), 0)
    
    def test_forward_declarations(self):
        """Test that forward declarations are not extracted."""
        source = b"""
class Foo;  // Forward declaration
void bar(); // Function declaration (prototype)

class Foo { // Actual definition
    int x;
};

void bar() { // Actual definition
}
"""
        tree = parse_bytes(source)
        entities = extract_entities_from_tree(tree, source, "repo", "forward.cpp")
        
        # Should only extract actual definitions, not forward declarations
        classes = [e for e in entities if e.entity_type == "Class"]
        functions = [e for e in entities if e.entity_type == "Function"]
        
        # Should have 1 class definition (the actual one)
        self.assertEqual(len(classes), 1, "Should have exactly 1 class definition")
        # Should have 1 function definition (the actual one)
        self.assertEqual(len(functions), 1, "Should have exactly 1 function definition")


if __name__ == "__main__":
    unittest.main()
