#!/usr/bin/env bash
# UAV-AI Linux Build Script
# Builds the desktop application using PyInstaller

set -e

echo "=== UAV-AI Linux Build ==="
echo

# Check Python is available
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 is not installed or not in PATH."
    exit 1
fi

# Install project dependencies (includes pyinstaller)
echo "Installing project dependencies..."
pip install -r requirements.txt

echo
echo "Building UAV-AI executable..."
python3 -m PyInstaller uav-ai.spec --noconfirm

echo
echo "=== Build complete! ==="
echo "Executable: dist/UAV-AI/UAV-AI"
echo
echo "To run: ./dist/UAV-AI/UAV-AI"
