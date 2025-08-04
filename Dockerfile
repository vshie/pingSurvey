FROM vshie/simplepingsurvey-base:latest

# Install additional Python dependencies that might be missing from base image
RUN pip install --no-cache-dir --prefer-binary \
    scipy==1.11.1 \
    numpy==1.24.3 \
    pandas==2.0.3 \
    shapely==2.0.1 \
    folium==0.12.1 \
    branca==0.4.2 \
    geojson==2.5.0 \
    mercantile==1.2.1 \
    Pillow==9.0.1

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
