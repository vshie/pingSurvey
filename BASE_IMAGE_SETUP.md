# Base Image Setup for Faster Builds

This repository uses a base image to significantly speed up Docker builds by pre-installing all system dependencies and Python packages.

## How It Works

1. **Base Image** (`Dockerfile.base`): Contains all system dependencies and Python packages
2. **Main Image** (`Dockerfile`): Uses the base image and only copies application code

## Build Process

### First Time Setup

1. **Build the base image** (takes ~5 hours):
   ```bash
   # This will build and push the base image to Docker Hub
   git push origin main  # Triggers the build-base.yml workflow
   ```

2. **Build the main image** (takes ~5 minutes):
   ```bash
   # This will build the main image using the base image
   git push origin main  # Triggers the deploy-custom.yml workflow
   ```

### Subsequent Builds

- **Base image changes**: Only rebuild when `Dockerfile.base` changes
- **Application changes**: Builds in ~5 minutes using cached base image
- **Weekly updates**: Base image rebuilds automatically every Sunday

## Workflow Files

- `.github/workflows/build-base.yml`: Builds and pushes the base image
- `.github/workflows/deploy-custom.yml`: Builds the main image using the base image
- `.github/workflows/deploy.yml`: Original BlueOS community workflow (kept for reference)

## Docker Images

- **Base image**: `your-username/simplepingsurvey-base:latest`
- **Main image**: `your-username/simplepingsurvey:latest`

## Benefits

- **5+ hour builds** → **5 minute builds** for application changes
- **Consistent dependencies** across all builds
- **Automatic updates** via scheduled base image rebuilds
- **Multi-platform support** (AMD64, ARM64, ARM/v7)

## Manual Base Image Build

To manually trigger a base image build:

1. Go to GitHub Actions
2. Select "Build Base Image" workflow
3. Click "Run workflow" → "Run workflow"

## Troubleshooting

### Base Image Not Found
If the main build fails with "base image not found":
1. Ensure the base image was built successfully
2. Check Docker Hub for `your-username/simplepingsurvey-base:latest`
3. Manually trigger the base image build workflow

### Dependency Updates
To update dependencies:
1. Modify `Dockerfile.base`
2. Push changes to trigger base image rebuild
3. Wait for base image build to complete
4. Push application changes to trigger main image build 