FROM python:3.11-slim

# Install system dependencies for bathymetry map generation (ARM-optimized)
RUN apt-get update && \
    apt-get install -y \
    gcc \
    python3-dev \
    python3-pip \
    python3-venv \
    python3-pandas \
    python3-numpy \
    python3-scipy \
    python3-matplotlib \
    python3-pil \
    python3-requests \
    python3-shapely \
    libgeos-dev \
    libproj-dev \
    proj-data \
    proj-bin \
    libgdal-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

# Copy the contour map generator files (handle space in directory name)
COPY "contour map generator" /app/contour_map_generator

# Copy app files
COPY app /app

# Upgrade pip and install build tools
RUN pip install --upgrade pip setuptools wheel

# Install Python dependencies using ARM-optimized methods from the Pi script
# Method 1: Try pre-compiled wheels first (ARM-optimized)
RUN pip install --no-cache-dir --prefer-binary \
    folium==0.12.1 \
    branca==0.4.2 \
    geojson==2.5.0 \
    requests==2.28.2 \
    mercantile==1.2.1 \
    Pillow==9.0.1

# Method 2: Install core scientific packages with ARM flags
RUN pip install --no-cache-dir --prefer-binary \
    numpy>=1.21.0 \
    pandas>=1.3.0 \
    scipy>=1.7.0 \
    shapely>=1.8.0

# Method 3: Try to install contextily (satellite background) with fallback
RUN pip install --no-cache-dir --prefer-binary contextily==1.2.0 || \
    (echo "Contextily installation failed, using fallback method" && \
     pip install --no-deps contextily==1.2.0 || \
     echo "Contextily not available - maps will work without satellite background")

# Install the main app
RUN python -m pip install /app --extra-index-url https://www.piwheels.org/simple

EXPOSE 5420/tcp

LABEL version="1.0.1"

ARG IMAGE_NAME

LABEL permissions='\
{\
  "ExposedPorts": {\
    "5420/tcp": {}\
  },\
  "HostConfig": {\
    "Binds":["/usr/blueos/extensions/ping-survey:/app/logs"],\
    "ExtraHosts": ["host.docker.internal:host-gateway"],\
    "PortBindings": {\
      "5420/tcp": [\
        {\
          "HostPort": ""\
        }\
      ]\
    }\
  }\
}'

ARG AUTHOR
ARG AUTHOR_EMAIL
LABEL authors='[\
    {\
        "name": "Tony White",\
        "email": "tonywhite@bluerobotics.com"\
    }\
]'

ARG MAINTAINER
ARG MAINTAINER_EMAIL
LABEL company='{\
        "about": "",\
        "name": "Blue Robotics",\
        "email": "support@bluerobotics.com"\
    }'
LABEL type="tool"
ARG REPO
ARG OWNER
LABEL readme='https://raw.githubusercontent.com/vshie/pingSurvey/{tag}/README.md'
LABEL links='{\
        "source": "https://github.com/vshie/pingSurvey"\
    }'
LABEL requirements="core >= 1.1"

ENTRYPOINT ["python", "-u", "/app/main.py"]
