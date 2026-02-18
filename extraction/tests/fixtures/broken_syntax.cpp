// This file intentionally has syntax errors for error resilience testing

void valid_function() {
    return;
}

// Missing closing brace - syntax error
void broken_function() {
    int x = 10;
    // Missing }

void another_valid() {
    // This should still parse
}
