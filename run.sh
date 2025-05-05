#!/bin/bash

# Make sure we're in the project root directory
cd "$(dirname "$0")"

# Check if python-dotenv is installed
python3 -m pip show python-dotenv > /dev/null 2>&1
if [ $? -ne 0 ]; then
  echo "Installing python-dotenv..."
  python3 -m pip install python-dotenv
fi

# Check if the .env file exists
if [ ! -f .env ]; then
  echo "Error: .env file not found!"
  echo "Please create a .env file with your API keys:"
  echo "GOOGLE_API_KEY=your-gemini-api-key"
  echo "OPENAI_API_KEY=your-openai-api-key"
  exit 1
fi

# Create static directory if it doesn't exist
mkdir -p static

# Clean static directory to avoid conflicts
echo "Cleaning static directory..."
rm -rf static/*

# Copy frontend files to static directory
echo "Copying frontend files to static directory..."
cp -r frontend/* static/

# Verify files were copied
echo "Verifying frontend files..."
if [ -f "static/index.html" ]; then
  echo "✅ Frontend files copied successfully"
else
  echo "❌ Failed to copy frontend files"
  exit 1
fi

# Run the application
echo "Starting PlotTwist application..."
# Use port 8080 instead of 8000
python3 backend/main.py --port 8080
