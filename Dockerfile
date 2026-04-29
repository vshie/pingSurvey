FROM python:3.11-slim

# RUN apt-get update && \
#    apt-get -y install gcc && \
#    rm -rf /var/lib/apt/lists/*

COPY app /app
RUN python -m pip install /app --extra-index-url https://www.piwheels.org/simple

EXPOSE 5420/tcp

LABEL version="1.2.1"
LABEL name="simple_ping_survey"
LABEL display_name="Simple Ping Survey"
LABEL display_description="Record Ping sonar, GPS, and vehicle attitude data to survey CSV logs"
LABEL display_category="Sensors"
LABEL display_icon="mdi-map-plus"
LABEL display_order="10"
LABEL description="Simple Ping Survey records Ping sonar and navigation data to CSV logs"
LABEL icon="mdi-map-plus"

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
          "HostPort": "5420"\
        }\
      ]\
    },\
    "NetworkMode": "host"\
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
