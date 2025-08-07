FROM vshie/simplepingsurvey-base:latest
# Using updated base image with multi-platform manifest

# Note: Most packages are installed in the base image, but we need to verify and handle any missing ones
# System packages (numpy, pandas) are installed via apt-get and may need pip equivalents for some use cases

# Verify and install any missing packages
RUN python3 -c "import numpy, scipy, folium, shapely; print('Core packages available')" && \
    python3 -c "import pandas; print('pandas available')" || \
    (echo "pandas not available via python3, installing via pip" && \
     pip install --no-cache-dir --prefer-binary pandas==2.0.3)

# Copy application files (these change most frequently)
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
