#pragma once

/// Point in 2D space
struct Point {
    double x;
    double y;
};

/// Vector operations
namespace vector {

/// Calculate dot product
double dot(const Point& a, const Point& b);

} // namespace vector
