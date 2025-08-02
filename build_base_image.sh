#!/bin/bash

# Script to manually build and push the base image
# This is a fallback if the GitHub Actions workflow doesn't trigger

echo "Building base image..."

# Build the base image for all platforms
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -f Dockerfile.base \
  -t vshie/simplepingsurvey-base:latest \
  --push \
  .

echo "Base image build complete!"
echo "You can now build the main image using the base image." 