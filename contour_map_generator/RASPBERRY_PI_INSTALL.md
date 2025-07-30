# Raspberry Pi Installation Guide - With Satellite Background

## 🚀 Quick Start (Recommended)

### Step 1: System Setup
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install system dependencies
sudo apt install -y python3-pip python3-venv python3-dev
sudo apt install -y python3-pandas python3-numpy python3-scipy python3-matplotlib
sudo apt install -y python3-pil python3-requests python3-shapely
sudo apt install -y libgeos-dev libproj-dev proj-data proj-bin libgdal-dev gdal-bin
```

### Step 2: Create Virtual Environment
```bash
# Create virtual environment
python3 -m venv bathymap_env
source bathymap_env/bin/activate

# Upgrade pip
pip3 install --upgrade pip setuptools wheel
```

### Step 3: Install Python Packages
```bash
# Install core packages
pip3 install --no-cache-dir --prefer-binary folium==0.12.1
pip3 install --no-cache-dir --prefer-binary branca==0.4.2
pip3 install --no-cache-dir --prefer-binary geojson==2.5.0
pip3 install --no-cache-dir --prefer-binary requests==2.28.2
pip3 install --no-cache-dir --prefer-binary mercantile==1.2.1
pip3 install --no-cache-dir --prefer-binary Pillow==9.0.1
```

### Step 4: Install Satellite Background (Optional)
```bash
# Try to install contextily for satellite background
./install_contextily_raspberry_pi.sh
```

## 📋 Complete One-Line Installation
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv python3-dev python3-pandas python3-numpy python3-scipy python3-matplotlib python3-pil python3-requests python3-shapely libgeos-dev libproj-dev proj-data proj-bin libgdal-dev gdal-bin && python3 -m venv bathymap_env && source bathymap_env/bin/activate && pip3 install --upgrade pip setuptools wheel && pip3 install --no-cache-dir --prefer-binary folium==0.12.1 branca==0.4.2 geojson==2.5.0 requests==2.28.2 mercantile==1.2.1 Pillow==9.0.1
```

## 🗺️ Run the Scripts

### Interactive Map (with satellite background)
```bash
# Activate environment
source bathymap_env/bin/activate

# Run interactive map
python3 interactive_bathymetry_map.py "your_data.csv"

# With custom settings
python3 interactive_bathymetry_map.py "your_data.csv" -p 10 -s 2 -o "my_map.html"
```

### Static Map (with satellite background)
```bash
# Activate environment
source bathymap_env/bin/activate

# Run static map
python3 bathymetry_contour_map.py "your_data.csv"

# With custom settings
python3 bathymetry_contour_map.py "your_data.csv" -p 10 -s 2 -o "my_map.png"
```

## 🔧 Troubleshooting

### If contextily installation fails:
The scripts will work without satellite background. You'll get:
- Interactive map: Plain background with contours
- Static map: Light blue background with contours

### If you get memory errors:
```bash
# Create swap file
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### If you get display errors (headless):
```bash
export MPLBACKEND=Agg
```

## ✅ Test Installation
```bash
# Test core packages
python3 -c "import pandas, numpy, matplotlib, scipy; print('✅ Core packages OK')"

# Test mapping packages
python3 -c "import folium, geojson; print('✅ Mapping packages OK')"

# Test satellite background (if installed)
python3 -c "import contextily; print('✅ Satellite background OK')" 2>/dev/null || echo "⚠️  Satellite background not available"
```

## 📁 File Structure After Installation
```
AI_bathymap/
├── interactive_bathymetry_map.py    # Interactive map script
├── bathymetry_contour_map.py        # Static map script
├── data_analysis.py                 # Data analysis script
├── requirements.txt                 # Python dependencies
├── install_contextily_raspberry_pi.sh # Satellite background installer
├── your_data.csv                    # Your bathymetry data
├── interactive_bathymetry_map.html  # Generated interactive map
├── bathymetry_contour_map.png       # Generated static map
└── depth_histogram.png              # Generated depth histogram
```

## 🎯 Features Available

### Interactive Map:
- ✅ Zoom and pan functionality
- ✅ Google satellite background (if contextily installed)
- ✅ Yellow primary contours (5m intervals)
- ✅ Red secondary contours (1m intervals)
- ✅ Toggleable data points
- ✅ Layer controls
- ✅ Measurement tools
- ✅ Fullscreen option
- ✅ Clickable depth histogram link

### Static Map:
- ✅ High-resolution PNG output
- ✅ Satellite background (if contextily installed)
- ✅ Contour lines with proper layering
- ✅ Colorbar and legend
- ✅ Data points overlay

## 🔄 Alternative Installation Methods

### Method 1: Use System Python (No Virtual Environment)
```bash
# Install everything via apt
sudo apt install -y python3-pandas python3-numpy python3-scipy python3-matplotlib
sudo apt install -y python3-pil python3-requests python3-shapely
sudo apt install -y python3-folium python3-branca python3-contextily

# Run directly
python3 interactive_bathymetry_map.py "your_data.csv"
```

### Method 2: Use Conda (if available)
```bash
# Install Miniconda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh
bash Miniconda3-latest-Linux-aarch64.sh

# Create environment
conda create -n bathymap python=3.9
conda activate bathymap

# Install packages
conda install -c conda-forge pandas numpy scipy matplotlib
conda install -c conda-forge folium contextily shapely geojson
```

## 📞 Support

If you encounter issues:
1. Check that all system dependencies are installed
2. Ensure you're in the virtual environment
3. Try the alternative installation methods
4. The scripts will work without satellite background if contextily fails

The core functionality (contour mapping) will work regardless of satellite background availability! 