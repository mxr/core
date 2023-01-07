"""Sensor for the CityBikes data."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Generic, TypeVar, Union

import aiohttp
import async_timeout
import citybikes
import voluptuous as vol

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    PLATFORM_SCHEMA,
    SensorEntity,
)
from homeassistant.const import (
    ATTR_ID,
    ATTR_LATITUDE,
    ATTR_LOCATION,
    ATTR_LONGITUDE,
    ATTR_NAME,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_RADIUS,
    UnitOfLength,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import location
from homeassistant.util.unit_conversion import DistanceConverter
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

_LOGGER = logging.getLogger(__name__)

ATTR_EMPTY_SLOTS = "empty_slots"
ATTR_EXTRA = "extra"
ATTR_FREE_BIKES = "free_bikes"
ATTR_NETWORK = "network"
ATTR_NETWORKS_LIST = "networks"
ATTR_STATIONS_LIST = "stations"
ATTR_TIMESTAMP = "timestamp"
ATTR_UID = "uid"

CONF_NETWORK = "network"
CONF_STATIONS_LIST = "stations"

PLATFORM = "citybikes"

MONITORED_NETWORKS = "monitored-networks"

REQUEST_TIMEOUT = 5  # In seconds; argument to asyncio.timeout

SCAN_INTERVAL = timedelta(minutes=5)  # Timely, and doesn't suffocate the API

CITYBIKES_ATTRIBUTION = (
    "Information provided by the CityBikes Project (https://citybik.es/#about)"
)

CITYBIKES_NETWORKS = "citybikes_networks"

PLATFORM_SCHEMA = vol.All(
    cv.has_at_least_one_key(CONF_RADIUS, CONF_STATIONS_LIST),
    PLATFORM_SCHEMA.extend(
        {
            vol.Optional(CONF_NAME, default=""): cv.string,
            vol.Optional(CONF_NETWORK): cv.string,
            vol.Inclusive(CONF_LATITUDE, "coordinates"): cv.latitude,
            vol.Inclusive(CONF_LONGITUDE, "coordinates"): cv.longitude,
            vol.Optional(CONF_RADIUS, "station_filter"): cv.positive_int,
            vol.Optional(CONF_STATIONS_LIST, "station_filter"): vol.All(
                cv.ensure_list, vol.Length(min=1), [cv.string]
            ),
        }
    ),
)

Resource = TypeVar(
    "Resource", bound=Union[dict[str, citybikes.Network], dict[str, citybikes.Station]]
)


class Fetcher(Generic[Resource]):
    """An interface for making a call to the CityBikes API with a timeout."""

    async def fetch(self, hass: HomeAssistant) -> Resource:
        """Perform a request to CityBikes API endpoint, and parse the response."""
        try:
            session = async_get_clientsession(hass)
            async with async_timeout.timeout(REQUEST_TIMEOUT):
                return await self.fetch_impl(session)
        except (asyncio.TimeoutError, aiohttp.ClientError):
            _LOGGER.error("Could not connect to CityBikes API endpoint")
        except ValueError:
            _LOGGER.error("Received non-JSON data from CityBikes API endpoint")
        raise CityBikesRequestError

    async def fetch_impl(self, session: aiohttp.ClientSession) -> Resource:
        """Make a CityBikes API request (must be implemented by children)."""
        raise NotImplementedError()


class CityBikesRequestError(Exception):
    """Error to indicate a CityBikes API request has failed."""


class NetworksFetcher(Fetcher):
    """Fetch networks."""

    def __init__(self, client: citybikes.Client) -> None:
        """Initialize the NetworksFetcher."""
        self.client = client

    async def fetch_impl(
        self, session: aiohttp.ClientSession
    ) -> dict[str, citybikes.Network]:
        """Refresh networks."""
        self.client.session = session
        self.client.networks.request()
        return {n[ATTR_ID]: n for n in self.client.networks}


class StationFetcher(Fetcher):
    """Fetch stations."""

    def __init__(self, network: citybikes.Network) -> None:
        """Initialize the StationFetcher."""
        self.network = network

    async def fetch_impl(
        self, session: aiohttp.ClientSession
    ) -> dict[str, citybikes.Station]:
        """Refresh stations on the network."""
        self.network.stations.client.session = session
        self.network.stations.request()
        return {s[ATTR_ID]: s for s in self.network.stations}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the CityBikes platform."""
    if PLATFORM not in hass.data:
        hass.data[PLATFORM] = {MONITORED_NETWORKS: {}}

    latitude = config.get(CONF_LATITUDE, hass.config.latitude)
    longitude = config.get(CONF_LONGITUDE, hass.config.longitude)
    network_id = config.get(CONF_NETWORK)
    stations_list = set(config.get(CONF_STATIONS_LIST, []))
    radius = config.get(CONF_RADIUS, 0)
    name = config[CONF_NAME]
    if hass.config.units is US_CUSTOMARY_SYSTEM:
        radius = DistanceConverter.convert(
            radius, UnitOfLength.FEET, UnitOfLength.METERS
        )

    citybikes_client = citybikes.Client()

    # Create a single instance of CityBikesNetworks.
    citybikes_networks = hass.data.setdefault(
        CITYBIKES_NETWORKS, CityBikesNetworks(hass, citybikes_client)
    )

    if not network_id:
        network_id = await citybikes_networks.get_closest_network_id(
            latitude, longitude
        )

    if network_id not in hass.data[PLATFORM][MONITORED_NETWORKS]:
        network = CityBikesNetwork(
            hass, citybikes_client, citybikes_networks.networks[network_id]
        )
        hass.data[PLATFORM][MONITORED_NETWORKS][network_id] = network
        hass.async_create_task(network.async_refresh())
        async_track_time_interval(hass, network.async_refresh, SCAN_INTERVAL)
    else:
        network = hass.data[PLATFORM][MONITORED_NETWORKS][network_id]

    await network.ready.wait()

    devices = []
    for station_id, station in network.stations.items():
        dist = location.distance(
            latitude, longitude, station[ATTR_LATITUDE], station[ATTR_LONGITUDE]
        )
        station_uid = str(station.get(ATTR_EXTRA, {}).get(ATTR_UID, ""))

        if radius > dist or stations_list.intersection((station_id, station_uid)):
            if name:
                uid = "_".join([network_id, name, station_id])
            else:
                uid = "_".join([network_id, station_id])
            entity_id = async_generate_entity_id(ENTITY_ID_FORMAT, uid, hass=hass)
            devices.append(CityBikesStation(network, station, entity_id))

    async_add_entities(devices, True)


class CityBikesNetworks:
    """Represent all CityBikes networks."""

    def __init__(self, hass: HomeAssistant, client: citybikes.Client) -> None:
        """Initialize the networks instance."""
        self.hass = hass
        self.client = client
        self.networks = None
        self.networks_loading = asyncio.Condition()

    async def get_closest_network_id(self, latitude, longitude):
        """Return the id of the network closest to provided location."""
        try:
            await self.networks_loading.acquire()
            if self.networks is None:
                self.networks = await NetworksFetcher(self.client).fetch(self.hass)
            result = None
            minimum_dist = None
            for network_id, network in self.networks.items():
                network_latitude = network[ATTR_LOCATION][ATTR_LATITUDE]
                network_longitude = network[ATTR_LOCATION][ATTR_LONGITUDE]
                dist = location.distance(
                    latitude, longitude, network_latitude, network_longitude
                )
                if minimum_dist is None or dist < minimum_dist:
                    minimum_dist = dist
                    result = network_id

            return result
        except CityBikesRequestError as err:
            raise PlatformNotReady from err
        finally:
            self.networks_loading.release()


class CityBikesNetwork:
    """Thin wrapper around a CityBikes network object."""

    def __init__(
        self, hass: HomeAssistant, client: citybikes.Client, network: citybikes.Network
    ) -> None:
        """Initialize the network object."""
        self.hass = hass
        self.client = client
        self.network = network
        self.stations: dict[str, citybikes.Station] = {}
        self.ready = asyncio.Event()

    async def async_refresh(self, now=None):
        """Refresh the state of the network."""
        try:
            self.stations = await StationFetcher(self.network).fetch(self.hass)
            self.ready.set()
        except CityBikesRequestError as err:
            if now is not None:
                self.ready.clear()
            else:
                raise PlatformNotReady from err


class CityBikesStation(SensorEntity):
    """CityBikes API Sensor."""

    _attr_attribution = CITYBIKES_ATTRIBUTION
    _attr_native_unit_of_measurement = "bikes"
    _attr_icon = "mdi:bike"

    def __init__(self, network, station, entity_id):
        """Initialize the sensor."""
        self._network = network
        self._station = station
        self.entity_id = entity_id

    async def async_update(self) -> None:
        """Update station state."""
        self._attr_name = self._station.get(ATTR_NAME)
        self._attr_native_value = self._station.get(ATTR_FREE_BIKES)
        self._attr_extra_state_attributes = {
            ATTR_UID: self._station.get(ATTR_EXTRA, {}).get(ATTR_UID),
            ATTR_LATITUDE: self._station.get(ATTR_LATITUDE),
            ATTR_LONGITUDE: self._station.get(ATTR_LONGITUDE),
            ATTR_EMPTY_SLOTS: self._station.get(ATTR_EMPTY_SLOTS),
            ATTR_TIMESTAMP: self._station.get(ATTR_TIMESTAMP),
        }
