#!/bin/bash
# ECR Build Script - Creates a single Linux binary
# 
# Prerequisites:
#   pip install pyinstaller
#
# Usage:
#   ./build.sh
#
# Output:
#   dist/ecr - Single executable binary

set -e

echo "=== ECR Build Script ==="
echo ""

# Check if pyinstaller is installed
if ! command -v pyinstaller &> /dev/null; then
    echo "PyInstaller not found. Installing..."
    pip install pyinstaller
fi

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf build dist __pycache__ *.egg-info

# Create empty directories if they don't exist
mkdir -p profiles runs web/static

# Build the binary
echo "Building single binary..."
pyinstaller ecr.spec --clean

# Check if build succeeded
if [ -f "dist/ecr" ]; then
    echo ""
    echo "=== Build Successful ==="
    echo "Binary created: dist/ecr"
    echo "Size: $(du -h dist/ecr | cut -f1)"
    echo ""
    echo "To run:"
    echo "  ./dist/ecr"
    echo ""
    echo "Options:"
    echo "  ./dist/ecr --host 0.0.0.0 --port 8080"
    echo "  ./dist/ecr --profiles-dir /path/to/profiles"
    echo ""
else
    echo "Build failed!"
    exit 1
fi
