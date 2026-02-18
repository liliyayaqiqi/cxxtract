#pragma once

/**
 * @brief Generic swap function
 * @tparam T Type of elements to swap
 */
template<typename T>
void swap(T& a, T& b) {
    T temp = a;
    a = b;
    b = temp;
}

/**
 * @brief A generic container class
 */
template<typename T>
class Container {
public:
    Container() : data_(nullptr) {}
    
    T* data_;
};

// Template specialization
template<>
class Container<bool> {
public:
    // Specialized implementation for bool
    bool value_;
};
