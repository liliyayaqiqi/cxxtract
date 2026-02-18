// Test fixture for Doxygen comment extraction

/**
 * @brief Multiply two integers
 * @param a First number
 * @param b Second number
 * @return Product of a and b
 */
int multiply(int a, int b) {
    return a * b;
}

/// Single-line Doxygen comment
/// Continued on next line
void single_line_doxygen() {
    // Implementation
}

//! Alternative Doxygen style
int alternative_style() {
    return 42;
}

/*! Block style Doxygen
 *  with multiple lines
 */
void block_doxygen() {
}

// Regular comment (not Doxygen)
void no_doxygen() {
}

void no_comment_at_all() {
}
