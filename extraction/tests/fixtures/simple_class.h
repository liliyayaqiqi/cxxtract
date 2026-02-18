#ifndef SIMPLE_CLASS_H
#define SIMPLE_CLASS_H

/**
 * @brief A simple calculator class
 * @details Provides basic arithmetic operations
 */
class Calculator {
public:
    /**
     * @brief Constructor
     */
    Calculator();
    
    /**
     * @brief Add two numbers
     */
    int add(int a, int b);
    
private:
    int result_;
};

/// A simple struct for testing
struct Point {
    int x;
    int y;
};

#endif // SIMPLE_CLASS_H
