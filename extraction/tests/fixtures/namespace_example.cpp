// Namespace example for testing

namespace math {

/// Calculate factorial
int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}

namespace constants {
    const double PI = 3.14159;
}

} // namespace math

namespace utils {

class Logger {
public:
    void log(const char* msg);
};

} // namespace utils
