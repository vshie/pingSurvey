# Bathymetry Contour Map Generator

A comprehensive bathymetry mapping system that generates both static and interactive contour plots with satellite imagery. Features advanced data filtering, configurable contour intervals, and a powerful click-to-mark coordinate tool.

## 🗺️ Scripts Included

1. **`bathymetry_contour_map.py`** - Static contour map (PNG output)
2. **`interactive_bathymetry_map.py`** - Interactive web map (HTML output) 
3. **`data_analysis.py`** - Data statistics and histogram generation

## ✨ Key Features

### 🎯 **Interactive Map (`interactive_bathymetry_map.py`)**
- **📱 Fully Interactive**: Zoom, pan, and navigate with mouse/touch
- **🛰️ Google Satellite Background**: High-resolution satellite imagery
- **🎨 Configurable Contours**: Customizable primary/secondary contour intervals
- **📍 Click-to-Mark Tool**: Click anywhere to get GPS coordinates copied to clipboard
- **📊 Layer Controls**: Toggle data points, contours, and different map layers
- **📏 Measurement Tools**: Measure distances and areas on the map
- **🖥️ Fullscreen Mode**: Expand to full screen for detailed viewing
- **📈 Embedded Histogram**: Clickable depth distribution chart
- **💾 Single File**: All data embedded in one portable HTML file

### 🖼️ **Static Map (`bathymetry_contour_map.py`)**
- **📊 High-Resolution PNG**: Print-quality contour maps
- **🎨 Professional Styling**: Clean, publication-ready graphics
- **⚙️ Configurable Intervals**: Customizable contour spacing via command line
- **🔍 Smart Filtering**: Automatic outlier removal and data validation

### 📊 **Data Analysis (`data_analysis.py`)**
- **📈 Depth Histogram**: Visual depth distribution analysis
- **📋 Statistical Summary**: Comprehensive data statistics
- **🎯 Quality Metrics**: Data validation and confidence analysis

## 🚀 Installation

### **Desktop Installation**
```bash
# Install Python dependencies
pip install -r requirements.txt

# For minimal installation (core features only)
pip install -r requirements_minimal.txt
```

### **Raspberry Pi Installation**
```bash
# Run the Raspberry Pi installation script
bash install_contextily_raspberry_pi.sh

# Or follow manual instructions in RASPBERRY_PI_INSTALL.md
```

## 📖 Usage

### **Interactive Map (Recommended)**
```bash
# Basic usage with default settings
python3 interactive_bathymetry_map.py "your_data.csv"

# Custom contour intervals (5m primary, 1m secondary)
python3 interactive_bathymetry_map.py "your_data.csv" -p 5 -s 1

# Custom output filename
python3 interactive_bathymetry_map.py "your_data.csv" -o "my_map.html"

# Full example with all options
python3 interactive_bathymetry_map.py "your_data.csv" -o "custom_map.html" -p 5 -s 1
```

### **Static Map**
```bash
# Basic usage
python3 bathymetry_contour_map.py "your_data.csv"

# Custom contour intervals
python3 bathymetry_contour_map.py "your_data.csv" -p 5 -s 1

# Custom output filename
python3 bathymetry_contour_map.py "your_data.csv" -o "my_map.png"
```

### **Data Analysis**
```bash
# Generate depth histogram and statistics
python3 data_analysis.py "your_data.csv"
```

## 🎮 Interactive Features

### **Click-to-Mark Coordinate Tool**
1. **Activate**: Click the "Click to Mark" button in the legend
2. **Mark Location**: Click anywhere on the map
3. **Get Coordinates**: GPS coordinates automatically copied to clipboard
4. **Visual Feedback**: Purple marker appears with green confirmation popup
5. **Move Marker**: Click elsewhere to move the marker and get new coordinates
6. **Clear**: Exit mark mode to remove the marker

### **Layer Controls**
- **Toggle Data Points**: Show/hide individual survey points
- **Toggle Contours**: Show/hide primary/secondary contour lines
- **Base Maps**: Switch between satellite and street views

### **Measurement Tools**
- **Distance Measurement**: Click to measure distances between points
- **Area Calculation**: Draw polygons to calculate areas
- **Unit Conversion**: Automatic conversion between meters/kilometers

## 📊 Data Format

The scripts expect a CSV file with these columns:
- `Latitude`: Decimal degrees (e.g., 47.123456)
- `Longitude`: Decimal degrees (e.g., -122.123456)
- `Depth (cm)`: Depth in centimeters (automatically converted to meters)
- `Confidence` (optional): Measurement confidence percentage

### **Example CSV Format**
```csv
Latitude,Longitude,Depth (cm),Confidence
47.123456,-122.123456,1250,95.2
47.123457,-122.123457,1300,94.8
```

## ⚙️ Command Line Options

### **Interactive Map Options**
```bash
python3 interactive_bathymetry_map.py <csv_file> [options]

Options:
  -o, --output FILE     Output HTML filename (default: interactive_bathymetry_map.html)
  -p, --primary FLOAT   Primary contour interval in meters (default: 5.0)
  -s, --secondary FLOAT Secondary contour interval in meters (default: 1.0)
```

### **Static Map Options**
```bash
python3 bathymetry_contour_map.py <csv_file> [options]

Options:
  -o, --output FILE     Output PNG filename (default: bathymetry_contour_map.png)
  -p, --primary FLOAT   Primary contour interval in meters (default: 5.0)
  -s, --secondary FLOAT Secondary contour interval in meters (default: 1.0)
```

## 🔧 Advanced Features

### **Data Filtering**
- **Invalid Coordinates**: Automatic removal of (0,0) and NaN values
- **Shallow Outliers**: Configurable minimum depth threshold (default: 5m)
- **Confidence Filtering**: Optional confidence threshold (default: 95%)
- **Spatial Filtering**: Density-based survey area masking
- **Distance Filtering**: Automatic outlier removal based on survey center

### **Contour Generation**
- **Spatial Interpolation**: Linear interpolation for smooth contours
- **Survey Area Masking**: Contours only in well-surveyed areas
- **Multi-level Contours**: Primary (yellow) and secondary (red) intervals
- **Proper Layering**: Primary contours plotted on top of secondary

### **Portability**
- **Single File Output**: All data embedded in HTML (no external dependencies)
- **Cross-Platform**: Works on Windows, macOS, Linux, and Raspberry Pi
- **Offline Capable**: Works without internet (except satellite tiles)
- **Easy Distribution**: Share single HTML files with no setup required

## 🐛 Troubleshooting

### **Common Issues**
- **Satellite tiles not loading**: Map will work with basic background
- **Large file sizes**: Normal for embedded data (6-10MB typical)
- **Memory issues**: Reduce dataset size or use static map option
- **Missing dependencies**: Check `requirements.txt` installation

### **Performance Tips**
- **Large datasets**: Use static maps for datasets >10,000 points
- **Slow loading**: Allow 10-30 seconds for large embedded maps
- **Browser compatibility**: Works best in Chrome, Firefox, Safari

## 📁 File Structure

```
AI_bathymap/
├── interactive_bathymetry_map.py    # Main interactive map generator
├── bathymetry_contour_map.py        # Static map generator
├── data_analysis.py                  # Data analysis and histogram
├── requirements.txt                  # Full dependencies
├── requirements_minimal.txt          # Minimal dependencies
├── RASPBERRY_PI_INSTALL.md          # Pi installation guide
├── install_contextily_raspberry_pi.sh # Pi setup script
└── README.md                        # This file
```

## 🎯 Example Output

### **Interactive Map Features**
- **Zoomable satellite imagery** with bathymetry contours
- **Click-to-mark tool** for GPS coordinate capture
- **Layer controls** for data points and contours
- **Measurement tools** for distances and areas
- **Embedded depth histogram** with clickable link
- **Fullscreen mode** for detailed viewing

### **Static Map Features**
- **High-resolution PNG** suitable for printing
- **Professional styling** with clear contour lines
- **Color-coded depth** visualization
- **Automatic scaling** to data extent
- **Publication-ready** graphics

## 🤝 Contributing

This project is designed for bathymetry data visualization. Key features include:
- **Universal compatibility** with bathymetry CSV files
- **Portable single-file output** for easy sharing
- **Advanced data filtering** for quality results
- **Interactive coordinate capture** for fieldwork
- **Cross-platform support** including Raspberry Pi

## 📄 License

This project is designed for bathymetry data visualization and mapping applications. 