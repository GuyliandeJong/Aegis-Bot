"""Docker containers through Portainer: /container, crash alerts, Watchtower updates."""

from core.modules import Module


MODULE = Module(
    name="containers",
    description="Docker containers through Portainer: /container, crash alerts, Watchtower updates.",
    required=(
        "PORTAINER_URL",
        "PORTAINER_API_KEY",
        "PORTAINER_ENDPOINT_ID",
    ),
    monitors=(
        "modules.containers.container_monitor:ContainerMonitor",
    ),
)
