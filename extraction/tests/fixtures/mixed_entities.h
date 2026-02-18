#pragma once

// Test fixture with classes, structs, and functions

/// A coordinate point
struct Point {
    int x;
    int y;
};

/// Rectangle class
class Rectangle {
public:
    Rectangle(int w, int h);
    int area();
private:
    int width_;
    int height_;
};

/// Calculate distance between points
double distance(const Point& a, const Point& b);

/// Circle struct
struct Circle {
    Point center;
    double radius;
};

/// Helper function
inline void helper() {
}
