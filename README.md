# BlueOS-ping Survey
# A simple extension for BlueOS intended for collection of time-sync'd sonar and GPS data. 

Relevant configuration: 
Extension Identifier: 
vshie.blueos-simplepingsurvey
Extension Name:
blueos-simplepingsurvey
Docker image:
vshie/blueo-simplepingsurvey
Docker tag:
main

Custom Settings:
{
  "ExposedPorts": {
    "5420/tcp": {}
  },
  "HostConfig": {
    "Binds":["/root/.config/blueos/extensions/$IMAGE_NAME:/root/.config"],
    "ExtraHosts": ["host.docker.internal:host-gateway"],
    "PortBindings": {
      "5420/tcp": [
        {
          "HostPort": ""
        }
      ]
    }
  }
}
