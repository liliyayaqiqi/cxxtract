// Test fixture for template entity extraction

/// Template function with one parameter
template<typename T>
T max_value(T a, T b) {
    return (a > b) ? a : b;
}

/// Template function with multiple parameters
template<typename T, typename U>
void print_pair(T first, U second) {
    // Implementation
}

/// Template class
template<typename T>
class Stack {
public:
    void push(T value);
    T pop();
private:
    T* data;
};

/// Template struct
template<typename K, typename V>
struct KeyValue {
    K key;
    V value;
};

// Non-template for comparison
void regular_function() {
}
