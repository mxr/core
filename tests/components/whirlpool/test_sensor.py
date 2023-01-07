"""Test the Whirlpool Sensor domain."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from whirlpool.washerdryer import MachineState

from homeassistant.core import CoreState, HomeAssistant, State
from homeassistant.helpers import entity_registry

from . import init_integration

from tests.common import mock_restore_cache_with_extra_data


async def update_sensor_state(
    hass: HomeAssistant,
    entity_id: str,
    mock_sensor_api_instance: MagicMock,
):
    """Simulate an update trigger from the API."""

    for call in mock_sensor_api_instance.register_attr_callback.call_args_list:
        update_ha_state_cb = call[0][0]
        update_ha_state_cb()
        await hass.async_block_till_done()

    return hass.states.get(entity_id)


def side_effect_function_open_door(*args, **kwargs):
    """Return correct value for attribute."""
    if args[0] == "Cavity_TimeStatusEstTimeRemaining":
        return 3540

    if args[0] == "Cavity_OpStatusDoorOpen":
        return "1"

    if args[0] == "WashCavity_OpStatusBulkDispense1Level":
        return "3"


async def test_dryer_sensor_values(
    hass: HomeAssistant,
    mock_sensor_api_instances: MagicMock,
    mock_sensor2_api: MagicMock,
):
    """Test the sensor value callbacks."""
    await init_integration(hass)

    entity_id = "sensor.dryer_state"
    mock_instance = mock_sensor2_api
    registry = entity_registry.async_get(hass)
    entry = registry.async_get(entity_id)
    assert entry
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "Standby"

    state = await update_sensor_state(hass, entity_id, mock_instance)
    assert state is not None
    state_id = f"{entity_id.split('_')[0]}_end_time"
    state = hass.states.get(state_id)
    assert state is not None

    mock_instance.get_machine_state.return_value = MachineState.RunningMainCycle
    mock_instance.get_cycle_status_filling.return_value = False
    mock_instance.attr_value_to_bool.side_effect = [
        False,
        False,
        False,
        False,
        False,
        False,
    ]

    state = await update_sensor_state(hass, entity_id, mock_instance)
    assert state is not None
    assert state.state == "Running Maincycle"

    mock_instance.get_machine_state.return_value = MachineState.Complete

    state = await update_sensor_state(hass, entity_id, mock_instance)
    assert state is not None
    assert state.state == "Complete"


async def test_washer_sensor_values(
    hass: HomeAssistant,
    mock_sensor_api_instances: MagicMock,
    mock_sensor1_api: MagicMock,
):
    """Test the sensor value callbacks."""
    await init_integration(hass)

    entity_id = "sensor.washer_state"
    mock_instance = mock_sensor1_api
    registry = entity_registry.async_get(hass)
    entry = registry.async_get(entity_id)
    assert entry
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "Standby"

    state = await update_sensor_state(hass, entity_id, mock_instance)
    assert state is not None
    state_id = f"{entity_id.split('_')[0]}_end_time"
    state = hass.states.get(state_id)
    assert state is not None

    state_id = f"{entity_id.split('_')[0]}_detergent_level"
    state = hass.states.get(state_id)
    assert state is not None
    assert state.state == "50%"

    # Test the washer cycle states
    mock_instance.get_machine_state.return_value = MachineState.RunningMainCycle
    mock_instance.get_cycle_status_filling.return_value = True
    mock_instance.attr_value_to_bool.side_effect = [
        True,
        False,
        False,
        False,
        False,
        False,
    ]

    state = await update_sensor_state(hass, entity_id, mock_instance)
    assert state is not None
    assert state.state == "Cycle Filling"

    mock_instance.get_cycle_status_filling.return_value = False
    mock_instance.get_cycle_status_rinsing.return_value = True
    mock_instance.attr_value_to_bool.side_effect = [
        False,
        True,
        False,
        False,
        False,
        False,
    ]

    state = await update_sensor_state(hass, entity_id, mock_instance)
    assert state is not None
    assert state.state == "Cycle Rinsing"

    mock_instance.get_cycle_status_rinsing.return_value = False
    mock_instance.get_cycle_status_sensing.return_value = True
    mock_instance.attr_value_to_bool.side_effect = [
        False,
        False,
        True,
        False,
        False,
        False,
    ]

    state = await update_sensor_state(hass, entity_id, mock_instance)
    assert state is not None
    assert state.state == "Cycle Sensing"

    mock_instance.get_cycle_status_sensing.return_value = False
    mock_instance.get_cycle_status_soaking.return_value = True
    mock_instance.attr_value_to_bool.side_effect = [
        False,
        False,
        False,
        True,
        False,
        False,
    ]

    state = await update_sensor_state(hass, entity_id, mock_instance)
    assert state is not None
    assert state.state == "Cycle Soaking"

    mock_instance.get_cycle_status_soaking.return_value = False
    mock_instance.get_cycle_status_spinning.return_value = True
    mock_instance.attr_value_to_bool.side_effect = [
        False,
        False,
        False,
        False,
        True,
        False,
    ]

    state = await update_sensor_state(hass, entity_id, mock_instance)
    assert state is not None
    assert state.state == "Cycle Spinning"

    mock_instance.get_cycle_status_spinning.return_value = False
    mock_instance.get_cycle_status_washing.return_value = True
    mock_instance.attr_value_to_bool.side_effect = [
        False,
        False,
        False,
        False,
        False,
        True,
    ]

    state = await update_sensor_state(hass, entity_id, mock_instance)
    assert state is not None
    assert state.state == "Cycle Washing"

    mock_instance.get_machine_state.return_value = MachineState.Complete
    mock_instance.attr_value_to_bool.side_effect = None
    mock_instance.get_attribute.side_effect = side_effect_function_open_door
    state = await update_sensor_state(hass, entity_id, mock_instance)
    assert state is not None
    assert state.state == "Door open"


async def test_restore_state(
    hass: HomeAssistant,
    mock_sensor_api_instances: MagicMock,
):
    """Test sensor restore state."""
    # Home assistant is not running yet
    hass.state = CoreState.not_running
    thetimestamp: datetime = datetime(2022, 11, 29, 00, 00, 00, 00, timezone.utc)
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(
                    "sensor.washer_end_time",
                    "1",
                ),
                {"native_value": thetimestamp, "native_unit_of_measurement": None},
            ),
            (
                State("sensor.dryer_end_time", "1"),
                {"native_value": thetimestamp, "native_unit_of_measurement": None},
            ),
        ),
    )

    # create and add entry
    await init_integration(hass)
    # restore from cache
    state = hass.states.get("sensor.washer_end_time")
    assert state.state == thetimestamp.isoformat()
    state = hass.states.get("sensor.dryer_end_time")
    assert state.state == thetimestamp.isoformat()
