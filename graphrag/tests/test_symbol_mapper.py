"""
Unit tests for symbol_mapper.py — SCIP symbol to Global URI conversion
and smart namespace filtering.
"""

import unittest
from unittest.mock import patch

from graphrag.symbol_mapper import (
    parse_scip_symbol,
    scip_symbol_to_global_uri,
    scip_symbol_to_entity_name,
    is_external_symbol,
    classify_symbol,
    resolve_symbol_owner_repo,
    should_drop_symbol,
    SCIP_KIND_CLASS,
    SCIP_KIND_STRUCT,
)


class TestParseScipSymbol(unittest.TestCase):
    """Test SCIP symbol string parsing."""
    
    def test_parse_class(self):
        """Test parsing a class symbol."""
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/GraphBuilderAdapter#",
            kind=SCIP_KIND_CLASS
        )
        
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Class")
        self.assertEqual(parsed.entity_name, "YAML::GraphBuilderAdapter")
        self.assertFalse(parsed.is_external)
    
    def test_parse_struct(self):
        """Test parsing a struct symbol with proper kind."""
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/Node#",
            kind=SCIP_KIND_STRUCT
        )
        
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Struct")
        self.assertEqual(parsed.entity_name, "YAML::Node")
    
    def test_parse_method(self):
        """Test parsing a method symbol."""
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/GraphBuilderAdapter#OnSequenceStart(ff993a8f75aba5c3).",
            kind=0
        )
        
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Function")
        self.assertEqual(parsed.entity_name, "YAML::GraphBuilderAdapter::OnSequenceStart")
    
    def test_parse_free_function(self):
        """Test parsing a free function (method suffix but no parent type)."""
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/EncodeBase64(556d3a62ec161185).",
            kind=0
        )
        
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Function")
        self.assertEqual(parsed.entity_name, "YAML::EncodeBase64")
    
    def test_parse_term(self):
        """Test parsing a term (static member or free function)."""
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/encoding.",
            kind=0
        )
        
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Function")
        self.assertEqual(parsed.entity_name, "YAML::encoding")
    
    def test_parse_external_std(self):
        """Test parsing an external std:: symbol."""
        parsed = parse_scip_symbol(
            "cxx . . $ std/runtime_error#",
            kind=SCIP_KIND_CLASS
        )
        
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_name, "std::runtime_error")
        self.assertTrue(parsed.is_external)
    
    def test_parse_external_std_nested(self):
        """Test parsing a nested std:: symbol."""
        parsed = parse_scip_symbol(
            "cxx . . $ std/__1/string#",
            kind=0
        )
        
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_name, "std::__1::string")
        self.assertTrue(parsed.is_external)
    
    def test_parse_local_returns_none(self):
        """Test that local symbols return None."""
        parsed = parse_scip_symbol("local 42", kind=0)
        self.assertIsNone(parsed)
    
    def test_parse_macro_returns_none(self):
        """Test that macro symbols return None."""
        parsed = parse_scip_symbol(
            "cxx . . $ `include/yaml-cpp/dll.h:52:11`!",
            kind=0
        )
        self.assertIsNone(parsed)
    
    def test_parse_backtick_escaped_names(self):
        """Test parsing symbols with backtick-escaped names."""
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/BadSubscript#`~BadSubscript`(49f6e7a06ebc5aa8).",
            kind=0
        )
        
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Function")
        # The destructor name includes the ~
        self.assertIn("~BadSubscript", parsed.entity_name)
    
    def test_kind_struct_maps_to_struct(self):
        """Test that Kind=Struct produces Struct entity type."""
        from graphrag.symbol_mapper import SCIP_KIND_STRUCT
        
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/Token#",
            kind=SCIP_KIND_STRUCT
        )
        
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Struct")
        self.assertEqual(parsed.entity_name, "YAML::Token")
    
    def test_kind_method_maps_to_function(self):
        """Test that Kind=Method on a type suffix still produces Class
        (method follows the type in the descriptor chain)."""
        from graphrag.symbol_mapper import SCIP_KIND_METHOD
        
        # When Kind=Method but suffix is #, it means the Kind hints
        # this is actually a method owner. Since scip-clang doesn't
        # set Kind properly, this tests the _KIND_TO_ENTITY_TYPE fallback.
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/Foo#bar(hash).",
            kind=SCIP_KIND_METHOD
        )
        
        self.assertIsNotNone(parsed)
        # bar() overrides entity_type to Function regardless of Kind
        self.assertEqual(parsed.entity_type, "Function")
    
    def test_kind_constructor_maps_to_function(self):
        """Test that Kind=Constructor produces Function entity type."""
        from graphrag.symbol_mapper import SCIP_KIND_CONSTRUCTOR
        
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/Node#Node(abc123).",
            kind=SCIP_KIND_CONSTRUCTOR
        )
        
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Function")
    
    def test_kind_variable_is_dropped(self):
        """Test that Kind=Variable causes the symbol to be dropped."""
        from graphrag.symbol_mapper import SCIP_KIND_VARIABLE
        
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/someGlobal.",
            kind=SCIP_KIND_VARIABLE
        )
        
        self.assertIsNone(parsed)
    
    def test_kind_parameter_is_dropped(self):
        """Test that Kind=Parameter causes the symbol to be dropped."""
        from graphrag.symbol_mapper import SCIP_KIND_PARAMETER
        
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/Node#Node(abc).",
            kind=SCIP_KIND_PARAMETER
        )
        
        self.assertIsNone(parsed)
    
    def test_kind_field_is_dropped(self):
        """Test that Kind=Field causes the symbol to be dropped."""
        from graphrag.symbol_mapper import SCIP_KIND_FIELD
        
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/Node#m_data.",
            kind=SCIP_KIND_FIELD
        )
        
        self.assertIsNone(parsed)
    
    def test_kind_enum_is_dropped(self):
        """Test that Kind=Enum causes the symbol to be dropped."""
        from graphrag.symbol_mapper import SCIP_KIND_ENUM
        
        parsed = parse_scip_symbol(
            "cxx . . $ YAML/NodeType#",
            kind=SCIP_KIND_ENUM
        )
        
        self.assertIsNone(parsed)
    
    def test_kind_unspecified_uses_suffix(self):
        """Test that Kind=0 (UnspecifiedKind) falls back to descriptor suffix.
        This is the scip-clang default and must produce correct types."""
        # # suffix -> Class
        parsed = parse_scip_symbol("cxx . . $ YAML/Node#", kind=0)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Class")
        
        # (hash). suffix -> Function
        parsed = parse_scip_symbol("cxx . . $ YAML/parse(hash).", kind=0)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Function")
        
        # . suffix -> Function
        parsed = parse_scip_symbol("cxx . . $ YAML/encoding.", kind=0)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.entity_type, "Function")
    
    def test_entity_type_only_three_allowed(self):
        """Test that only Class, Struct, Function entity types are produced."""
        from graphrag.symbol_mapper import SCIP_KIND_STRUCT
        
        test_cases = [
            ("cxx . . $ YAML/Node#", 0, "Class"),
            ("cxx . . $ YAML/Token#", SCIP_KIND_STRUCT, "Struct"),
            ("cxx . . $ YAML/parse(hash).", 0, "Function"),
            ("cxx . . $ YAML/Node#begin(hash).", 0, "Function"),
            ("cxx . . $ YAML/encoding.", 0, "Function"),
        ]
        
        for scip_sym, kind, expected_type in test_cases:
            parsed = parse_scip_symbol(scip_sym, kind)
            self.assertIsNotNone(parsed, f"Expected non-None for {scip_sym}")
            self.assertIn(
                parsed.entity_type,
                {"Class", "Struct", "Function"},
                f"entity_type '{parsed.entity_type}' not in allowed set "
                f"for {scip_sym}",
            )
            self.assertEqual(parsed.entity_type, expected_type)


class TestScipSymbolToGlobalUri(unittest.TestCase):
    """Test SCIP symbol to Global URI conversion."""
    
    def test_class_uri(self):
        """Test Global URI for a class."""
        uri = scip_symbol_to_global_uri(
            "cxx . . $ YAML/GraphBuilderAdapter#",
            "src/contrib/graphbuilderadapter.h",
            "yaml-cpp",
            kind=SCIP_KIND_CLASS
        )
        
        self.assertEqual(
            uri,
            "yaml-cpp::src/contrib/graphbuilderadapter.h::Class::YAML::GraphBuilderAdapter"
        )
    
    def test_method_uri(self):
        """Test Global URI for a method."""
        uri = scip_symbol_to_global_uri(
            "cxx . . $ YAML/GraphBuilderAdapter#OnSequenceStart(hash).",
            "src/contrib/graphbuilderadapter.cpp",
            "yaml-cpp",
            kind=0
        )
        
        self.assertEqual(
            uri,
            "yaml-cpp::src/contrib/graphbuilderadapter.cpp::Function::YAML::GraphBuilderAdapter::OnSequenceStart"
        )
    
    def test_free_function_uri(self):
        """Test Global URI for a free function."""
        uri = scip_symbol_to_global_uri(
            "cxx . . $ YAML/EncodeBase64(hash).",
            "src/binary.cpp",
            "yaml-cpp",
            kind=0
        )
        
        self.assertEqual(
            uri,
            "yaml-cpp::src/binary.cpp::Function::YAML::EncodeBase64"
        )

    def test_function_uri_with_signature_discriminator_opt_in(self):
        """Function URI can include signature hash token when requested."""
        uri = scip_symbol_to_global_uri(
            "cxx . . $ YAML/EncodeBase64(556d3a62ec161185).",
            "src/binary.cpp",
            "yaml-cpp",
            kind=0,
            include_function_sig=True,
        )
        self.assertIn("::Function::YAML::EncodeBase64::sig_556d3a62ec161185", uri)
    
    def test_external_symbol_uri(self):
        """Test Global URI for external symbol."""
        uri = scip_symbol_to_global_uri(
            "cxx . . $ std/runtime_error#",
            "<external>",
            "yaml-cpp",
            kind=0
        )
        
        # External symbols use <external> as file_path
        self.assertIn("<external>", uri)
        self.assertIn("std::runtime_error", uri)
    
    def test_local_symbol_returns_none(self):
        """Test that local symbols return None."""
        uri = scip_symbol_to_global_uri(
            "local 123",
            "test.cpp",
            "repo",
            kind=0
        )
        self.assertIsNone(uri)


class TestScipSymbolToEntityName(unittest.TestCase):
    """Test entity name extraction."""
    
    def test_entity_name_extraction(self):
        """Test extracting just the entity name."""
        name = scip_symbol_to_entity_name(
            "cxx . . $ YAML/GraphBuilderAdapter#OnSequenceStart(hash)."
        )
        
        self.assertEqual(name, "YAML::GraphBuilderAdapter::OnSequenceStart")
    
    def test_local_symbol_returns_none(self):
        """Test that local symbols return None."""
        name = scip_symbol_to_entity_name("local 42")
        self.assertIsNone(name)


class TestIsExternalSymbol(unittest.TestCase):
    """Test external symbol detection."""
    
    def test_std_is_external(self):
        """Test that std:: symbols are detected as external."""
        self.assertTrue(is_external_symbol("cxx . . $ std/string#"))
        self.assertTrue(is_external_symbol("cxx . . $ std/__1/vector#"))
    
    def test_project_symbol_not_external(self):
        """Test that project symbols are not external."""
        self.assertFalse(is_external_symbol("cxx . . $ YAML/Node#"))
    
    def test_local_returns_false(self):
        """Test that local symbols return False."""
        self.assertFalse(is_external_symbol("local 123"))


class TestClassifySymbol(unittest.TestCase):
    """Test smart namespace filtering via classify_symbol()."""
    
    def test_ignored_std_drops(self):
        """Test that std:: symbols are dropped."""
        self.assertEqual(classify_symbol("cxx . . $ std/string#"), "drop")
        self.assertEqual(classify_symbol("cxx . . $ std/__1/vector#"), "drop")
    
    def test_ignored_gnu_drops(self):
        """Test that __gnu_cxx:: symbols are dropped."""
        self.assertEqual(classify_symbol("cxx . . $ __gnu_cxx/hash_map#"), "drop")
    
    def test_ignored_boost_drops(self):
        """Test that boost:: symbols are dropped."""
        self.assertEqual(classify_symbol("cxx . . $ boost/optional#"), "drop")
    
    def test_monitored_yaml_keeps(self):
        """Test that YAML:: symbols are kept."""
        self.assertEqual(
            classify_symbol("cxx . . $ YAML/Node#", kind=SCIP_KIND_CLASS),
            "keep",
        )
    
    def test_monitored_webrtc_keeps(self):
        """Test that webrtc:: symbols are kept."""
        self.assertEqual(
            classify_symbol("cxx . . $ webrtc/RtpSender#", kind=SCIP_KIND_CLASS),
            "keep",
        )
    
    def test_unknown_namespace_keeps(self):
        """Test that unknown namespaces default to keep (conservative)."""
        self.assertEqual(
            classify_symbol("cxx . . $ SomeUnknownLib/Foo#"),
            "keep",
        )
    
    def test_local_symbol_drops(self):
        """Test that local symbols are dropped."""
        self.assertEqual(classify_symbol("local 42"), "drop")
    
    def test_macro_symbol_drops(self):
        """Test that macro symbols are dropped."""
        self.assertEqual(
            classify_symbol("cxx . . $ `include/foo.h:1:1`!"),
            "drop",
        )


class TestShouldDropSymbol(unittest.TestCase):
    """Test the should_drop_symbol convenience predicate."""
    
    def test_std_is_dropped(self):
        """Test that std:: symbols return True for should_drop."""
        self.assertTrue(should_drop_symbol("cxx . . $ std/string#"))
    
    def test_yaml_not_dropped(self):
        """Test that YAML:: symbols return False for should_drop."""
        self.assertFalse(should_drop_symbol("cxx . . $ YAML/Node#"))
    
    def test_boost_is_dropped(self):
        """Test that boost:: symbols return True for should_drop."""
        self.assertTrue(should_drop_symbol("cxx . . $ boost/optional#"))


class TestCrossRepoStubClassification(unittest.TestCase):
    """Test classify_symbol behaviour for cross-repo stub scenarios.
    
    classify_symbol uses explicit local-definition context to distinguish
    local definitions from cross-repo references in monitored namespaces.
    """
    
    def test_monitored_but_external_is_stub(self):
        """Monitored symbol from non-local package should classify as stub."""
        self.assertEqual(
            classify_symbol("cxx cargo sibling v1.0.0 webrtc/RtpSender#"),
            "stub",
        )

    def test_monitored_symbol_local_override_keeps(self):
        """Explicit local-definition context should force keep for monitored symbols."""
        self.assertEqual(
            classify_symbol(
                "cxx cargo sibling v1.0.0 webrtc/RtpSender#",
                is_local_definition=True,
            ),
            "keep",
        )

    def test_monitored_symbol_nonlocal_override_stubs(self):
        """Explicit non-local context should produce stub for monitored symbols."""
        self.assertEqual(
            classify_symbol(
                "cxx . . $ webrtc/RtpSender#",
                is_local_definition=False,
            ),
            "stub",
        )
    
    def test_unknown_external_not_in_ignore_keeps(self):
        """Test that an external namespace NOT in IGNORED is kept."""
        # SomeLib is neither ignored nor monitored -> keep (conservative)
        self.assertEqual(
            classify_symbol("cxx . . $ SomeLib/Foo#"),
            "keep",
        )


class TestOwnerRepoResolution(unittest.TestCase):
    """Test namespace->owner-repo resolution for cross-repo stubs."""

    @patch.dict(
        "graphrag.symbol_mapper.MONITORED_NAMESPACE_OWNER_REPOS",
        {"webrtc": "repo-b"},
        clear=True,
    )
    def test_resolve_owner_repo_from_namespace_mapping(self):
        owner = resolve_symbol_owner_repo(
            "cxx . . $ webrtc/RtpSender#",
            current_repo_name="repo-a",
            kind=SCIP_KIND_CLASS,
        )
        self.assertEqual(owner, "repo-b")

    @patch.dict("graphrag.symbol_mapper.MONITORED_NAMESPACE_OWNER_REPOS", {}, clear=True)
    def test_resolve_owner_repo_falls_back_to_current_repo(self):
        owner = resolve_symbol_owner_repo(
            "cxx . . $ webrtc/RtpSender#",
            current_repo_name="repo-a",
            kind=SCIP_KIND_CLASS,
        )
        self.assertEqual(owner, "repo-a")


if __name__ == "__main__":
    unittest.main()
