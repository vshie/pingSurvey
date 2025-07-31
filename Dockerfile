FROM python:3.11-slim

# Install system dependencies for bathymetry map generation (ARM-optimized)
RUN apt-get update && \
    apt-get install -y \
    gcc \
    gfortran \
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

# Upgrade pip and install build tools
RUN pip install --upgrade pip setuptools wheel

# Install Python dependencies using ARM-optimized methods from the Pi script
# Method 1: Try pre-compiled wheels first (ARM-optimized)
RUN pip install --no-cache-dir --prefer-binary \
    flask==2.3.3 \
    requests==2.28.2 \
    folium==0.12.1 \
    branca==0.4.2 \
    geojson==2.5.0 \
    mercantile==1.2.1 \
    Pillow==9.0.1 \
    numpy==1.24.3 \
    pandas==2.0.3 \
    shapely==2.0.1

# Method 2: Install scipy with multiple fallback strategies (most problematic package)
RUN pip install --no-cache-dir --prefer-binary scipy==1.11.1 || \
    (echo "scipy 1.11.1 wheel not available, trying 1.10.1" && \
     pip install --no-cache-dir --prefer-binary scipy==1.10.1) || \
    (echo "scipy 1.10.1 wheel not available, trying 1.9.3" && \
     pip install --no-cache-dir --prefer-binary scipy==1.9.3) || \
    (echo "No scipy wheels available, using system package" && \
     echo "System scipy will be used from apt-get installation")

# Method 3: Try to install contextily (satellite background) with fallback
RUN pip install --no-cache-dir --prefer-binary contextily==1.2.0 || \
    (echo "Contextily installation failed, using fallback method" && \
     pip install --no-deps contextily==1.2.0 || \
     echo "Contextily not available - maps will work without satellite background")

# Copy application files last (these change most frequently)
COPY contour_map_generator/ /app/contour_map_generator/
COPY app /app

# No need to install /app as a package since we're copying files directly

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
