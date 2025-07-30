#!/bin/bash
# Install contextily on Raspberry Pi without compilation issues

echo "Installing contextily for Raspberry Pi..."

# Method 1: Try to install pre-compiled wheel
echo "Attempting to install pre-compiled contextily..."
pip3 install --no-cache-dir --prefer-binary contextily==1.2.0

# If that fails, try Method 2: Install dependencies first
if [ $? -ne 0 ]; then
    echo "Pre-compiled installation failed. Trying alternative method..."
    
    # Install rasterio dependencies first
    sudo apt install -y libgdal-dev gdal-bin libgeos-dev libproj-dev proj-data proj-bin
    
    # Try installing with specific flags
    pip3 install --no-cache-dir --prefer-binary rasterio==1.2.10
    pip3 install --no-cache-dir --prefer-binary contextily==1.2.0
fi

# If that still fails, try Method 3: Use conda (if available)
if [ $? -ne 0 ]; then
    echo "Pip installation failed. Trying conda method..."
    
    # Check if conda is available
    if command -v conda &> /dev/null; then
        conda install -c conda-forge contextily
    else
        echo "Conda not available. Installing minimal version..."
        # Install minimal version without problematic dependencies
        pip3 install --no-deps contextily==1.2.0
    fi
fi

# Test installation
echo "Testing contextily installation..."
python3 -c "import contextily; print('contextily installed successfully!')" 2>/dev/null

if [ $? -eq 0 ]; then
    echo "✅ contextily installed successfully!"
else
    echo "❌ contextily installation failed. Using fallback method..."
    echo "The scripts will work without satellite background."
fi 