// Test fixture for namespace qualification

namespace outer {

/// Function in outer namespace
void outer_function() {
}

namespace inner {

/// Function in nested namespace
void inner_function() {
}

/// Class in nested namespace
class InnerClass {
public:
    void method();
};

} // namespace inner

/// Class in outer namespace
class OuterClass {
public:
    void method();
};

} // namespace outer

// Global function (no namespace)
void global_function() {
}
