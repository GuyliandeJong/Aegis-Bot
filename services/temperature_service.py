from models.temperature import Temperature
from models.temperature_status import TemperatureStatus
from redfish.client import RedfishClient


IMPORTANT_SENSORS = (
    "cpu",
    "system board",
    "ambient",
    "inlet",
    "exhaust",
)


ORDER = {
    "cpu": 0,
    "system board": 1,
    "ambient": 2,
    "inlet": 3,
    "exhaust": 4,
}


class TemperatureService:
    async def get_temperature_status(self) -> TemperatureStatus:
        client = RedfishClient()

        try:
            thermal = await client.get_thermal()

            temperatures: list[Temperature] = []

            for sensor in thermal.get("Temperatures", []):
                name = sensor.get("Name", "")
                reading = sensor.get("ReadingCelsius")

                if reading is None:
                    continue

                if not any(
                    keyword in name.lower()
                    for keyword in IMPORTANT_SENSORS
                ):
                    continue

                temperatures.append(
                    Temperature(
                        name=name,
                        reading=float(reading),
                        status=sensor.get("Status", {}).get("Health", "Unknown"),
                    )
                )

            temperatures.sort(
                key=lambda t: next(
                    (
                        value
                        for key, value in ORDER.items()
                        if key in t.name.lower()
                    ),
                    99,
                )
            )

            return TemperatureStatus(
                temperatures=temperatures,
            )

        finally:
            await client.close()