"""Tests for temperature-based fan speed stages (issue #662)."""

from unittest.mock import MagicMock

from custom_components.dual_smart_thermostat.hvac_device.fan_speed import (
    FanSpeedControl,
)
from custom_components.dual_smart_thermostat.managers.feature_manager import (
    FeatureManager,
)


def _control(stages, hysteresis=0.0, modes=None, selected="auto") -> FanSpeedControl:
    hass = MagicMock()
    hass.states.get.return_value = None
    owner = MagicMock(spec=FeatureManager)
    owner.fan_speed_stages = stages
    owner.fan_speed_stages_hysteresis = hysteresis
    control = FanSpeedControl(hass, "fan.test_fan", owner)
    control.fan_modes = modes or ["auto", "low", "medium", "high"]
    control.uses_preset_modes = False
    control._is_climate = False
    control.current_fan_mode = None
    control.selected_fan_mode = selected
    return control


def test_stages_pick_highest_threshold_met():
    control = _control(
        [
            {"above": 0.5, "speed": "low"},
            {"above": 1.5, "speed": "medium"},
            {"above": 2.5, "speed": "high"},
        ]
    )
    assert control.resolve_staged_speed(0.2) is None
    assert control.resolve_staged_speed(0.5) == "low"
    assert control.resolve_staged_speed(1.0) == "low"
    assert control.resolve_staged_speed(1.5) == "medium"
    assert control.resolve_staged_speed(2.4) == "medium"
    assert control.resolve_staged_speed(2.5) == "high"


def test_stages_accept_percentages_for_percentage_fans():
    control = _control(
        [
            {"above": 0.5, "speed": 33},
            {"above": 1.5, "speed": 66},
            {"above": 2.5, "speed": 100},
        ]
    )
    assert control.resolve_staged_speed(0.6) == "low"
    assert control.resolve_staged_speed(1.6) == "medium"
    assert control.resolve_staged_speed(2.6) == "high"


def test_stages_hysteresis_holds_then_releases():
    control = _control(
        [
            {"above": 0.5, "speed": "low"},
            {"above": 1.5, "speed": "medium"},
            {"above": 2.5, "speed": "high"},
        ],
        hysteresis=0.2,
    )
    control.current_fan_mode = "high"
    # 2.4 is below the high band but within hysteresis: hold.
    assert control.resolve_staged_speed(2.4) == "high"
    # Clearly below: step down.
    assert control.resolve_staged_speed(2.2) == "medium"

    control.current_fan_mode = "low"
    assert control.resolve_staged_speed(0.4) == "low"
    assert control.resolve_staged_speed(0.2) is None


def test_feature_manager_sorts_stages():
    stages = FeatureManager._normalize_fan_speed_stages(
        [
            {"above": 2.5, "speed": "high"},
            {"above": 0.5, "speed": "low"},
            {"above": 1.5, "speed": "medium"},
        ]
    )
    assert [s["above"] for s in stages] == [0.5, 1.5, 2.5]
    assert FeatureManager._normalize_fan_speed_stages(None) is None
    assert FeatureManager._normalize_fan_speed_stages([]) is None


def _staged_config():
    return [
        {"above": 1.0, "speed": "low"},
        {"above": 3.0, "speed": "medium"},
        {"above": 5.0, "speed": "high"},
    ]


def test_auto_selection_enables_staging():
    control = _control(_staged_config(), selected="auto")
    assert control.should_auto_stage() is True


def test_fixed_selection_disables_staging():
    for fixed in ("low", "medium", "high"):
        control = _control(_staged_config(), selected=fixed)
        assert control.should_auto_stage() is False


def test_no_stages_disables_staging():
    control = _control(None, selected="auto")
    assert control.should_auto_stage() is False


def test_fan_without_auto_stages_until_fixed():
    control = _control(_staged_config(), modes=["low", "medium", "high"], selected=None)
    assert control.should_auto_stage() is True
    control.selected_fan_mode = "medium"
    assert control.should_auto_stage() is False


def test_restore_prefers_selection_over_legacy():
    control = _control(_staged_config(), selected="low")
    fake_self = MagicMock(spec=FeatureManager)
    fake_self._fan_speed = control

    old_state = MagicMock()
    old_state.attributes = {"fan_selection": "auto", "fan_mode": "high"}
    FeatureManager._restore_fan_mode(fake_self, old_state)
    assert control.selected_fan_mode == "auto"
    assert control.current_fan_mode == "auto"

    old_state.attributes = {"fan_mode": "low"}
    FeatureManager._restore_fan_mode(fake_self, old_state)
    assert control.selected_fan_mode == "low"
    assert control.current_fan_mode == "low"


def test_stage_change_allowed_without_prior_change():
    control = _control(_staged_config(), selected="auto")
    assert control._last_speed_change is None
    assert control.stage_change_allowed() is True


def test_stage_change_blocked_within_interval():
    from time import monotonic

    control = _control(_staged_config(), selected="auto")
    control.owner.fan_speed_stages_min_interval = 300.0
    control._last_speed_change = monotonic()
    assert control.stage_change_allowed() is False
    control._last_speed_change = monotonic() - 301
    assert control.stage_change_allowed() is True


def test_stage_change_allowed_when_user_initiated():
    from time import monotonic

    control = _control(_staged_config(), selected="auto")
    control.owner.fan_speed_stages_min_interval = 300.0
    control._last_speed_change = monotonic()
    # An explicit target change must not wait out the interval.
    assert control.stage_change_allowed(user_initiated=True) is True


def test_user_initiated_change_skips_hysteresis():
    control = _control(
        [
            {"above": 0.5, "speed": "low"},
            {"above": 1.5, "speed": "medium"},
            {"above": 2.5, "speed": "high"},
        ],
        hysteresis=0.5,
    )
    control.current_fan_mode = "high"
    # Inside the hold band: automatic keeps high, a user change steps down.
    assert control.resolve_staged_speed(2.2) == "high"
    assert control.resolve_staged_speed(2.2, immediate=True) == "medium"


def test_stage_change_zero_interval_disables_limit():
    from time import monotonic

    control = _control(_staged_config(), selected="auto")
    control.owner.fan_speed_stages_min_interval = 0
    control._last_speed_change = monotonic()
    assert control.stage_change_allowed() is True


def test_normalize_fan_interval():
    from datetime import timedelta

    assert FeatureManager._normalize_fan_interval(None) == 300.0
    assert FeatureManager._normalize_fan_interval(timedelta(minutes=5)) == 300.0
    assert FeatureManager._normalize_fan_interval(60) == 60.0
    assert FeatureManager._normalize_fan_interval(0) == 0.0
    assert FeatureManager._normalize_fan_interval(-5) == 0.0
    assert FeatureManager._normalize_fan_interval({"minutes": 2}) == 120.0
    assert FeatureManager._normalize_fan_interval("bogus") == 300.0


async def test_fan_on_with_ac_engages_staged_speed(hass):
    """When fan_on_with_ac is true and cooler turns on, fan turns on at staged speed."""
    from homeassistant.components import input_boolean, input_number
    from homeassistant.components.climate import HVACMode
    from homeassistant.components.climate.const import DOMAIN as CLIMATE
    from homeassistant.const import SERVICE_TURN_ON, SERVICE_TURN_OFF
    from homeassistant.core import callback, DOMAIN as HASS_DOMAIN
    from homeassistant.setup import async_setup_component
    from homeassistant.util.unit_system import METRIC_SYSTEM
    from custom_components.dual_smart_thermostat.const import DOMAIN
    from . import common, setup_sensor

    hass.config.units = METRIC_SYSTEM
    calls = []

    @callback
    def log_call(call):
        calls.append(call)
        eid = call.data.get("entity_id")
        if eid and call.service in (SERVICE_TURN_ON, SERVICE_TURN_OFF):
            new_st = "on" if call.service == SERVICE_TURN_ON else "off"
            cur_attrs = hass.states.get(eid).attributes if hass.states.get(eid) else {}
            hass.states.async_set(eid, new_st, cur_attrs)

    hass.services.async_register(HASS_DOMAIN, SERVICE_TURN_ON, log_call)
    hass.services.async_register(HASS_DOMAIN, SERVICE_TURN_OFF, log_call)
    hass.services.async_register("fan", "set_preset_mode", log_call)
    hass.services.async_register("fan", "set_percentage", log_call)

    assert await async_setup_component(
        hass, input_boolean.DOMAIN, {"input_boolean": {"cooler": None, "dummy": None}}
    )
    assert await async_setup_component(
        hass,
        input_number.DOMAIN,
        {
            "input_number": {
                "temp": {"name": "test", "initial": 10, "min": 0, "max": 40, "step": 1}
            }
        },
    )

    hass.states.async_set(
        "fan.test_fan",
        "off",
        {
            "preset_modes": ["auto", "low", "medium", "high"],
            "preset_mode": "low",
        },
    )

    assert await async_setup_component(
        hass,
        CLIMATE,
        {
            "climate": {
                "platform": DOMAIN,
                "name": "test",
                "heater": "input_boolean.dummy",
                "cooler": "input_boolean.cooler",
                "fan": "fan.test_fan",
                "fan_on_with_ac": True,
                "target_sensor": common.ENT_SENSOR,
                "initial_hvac_mode": HVACMode.OFF,
                "fan_speed_stages": [
                    {"above": 0.3, "speed": "low"},
                    {"above": 3.0, "speed": "medium"},
                    {"above": 5.0, "speed": "high"},
                ],
            }
        },
    )
    await hass.async_block_till_done()

    # Turn on thermostat in COOL mode with target 20
    await common.async_set_hvac_mode(hass, HVACMode.COOL)
    await common.async_set_temperature(hass, 20)
    await hass.async_block_till_done()

    # Sensor at 21 -> delta is 1.0 > 0.3, meets "low" stage
    calls.clear()
    setup_sensor(hass, 21)
    await hass.async_block_till_done()

    # Verify cooler turn_on service call
    cooler_turned_on = any(
        c.service == SERVICE_TURN_ON
        and c.data.get("entity_id") == "input_boolean.cooler"
        for c in calls
    )
    assert cooler_turned_on, f"Cooler was not turned on. Calls: {calls}"

    # Verify fan turned on and staged speed was applied
    fan_turned_on = any(
        c.service == SERVICE_TURN_ON and c.data.get("entity_id") == "fan.test_fan"
        for c in calls
    )
    assert fan_turned_on, f"Fan was not turned on. Calls: {calls}"

    # Verify climate reports cooling and auto fan mode
    state = hass.states.get("climate.test")
    assert state.attributes["hvac_action"] == "cooling"
    assert state.attributes["fan_mode"] == "auto"

    # Now drop temperature to 19 -> goal reached
    calls.clear()
    setup_sensor(hass, 19)
    await hass.async_block_till_done()

    # Verify cooler is OFF
    assert hass.states.get("input_boolean.cooler").state == "off"
    # Verify fan is turned off
    fan_turned_off = any(
        c.service == SERVICE_TURN_OFF and c.data.get("entity_id") == "fan.test_fan"
        for c in calls
    )
    assert (
        fan_turned_off
    ), f"Fan was not turned off when cooling satisfied. Calls: {calls}"


async def test_fan_on_with_ac_percentage_fan_stages(hass):
    """When fan_on_with_ac is true and fan is percentage-based, fan turns on and sets percentage."""
    from homeassistant.components import input_boolean, input_number
    from homeassistant.components.climate import HVACMode
    from homeassistant.components.climate.const import DOMAIN as CLIMATE
    from homeassistant.const import SERVICE_TURN_ON, SERVICE_TURN_OFF
    from homeassistant.core import callback, DOMAIN as HASS_DOMAIN
    from homeassistant.setup import async_setup_component
    from homeassistant.util.unit_system import METRIC_SYSTEM
    from custom_components.dual_smart_thermostat.const import DOMAIN
    from . import common, setup_sensor

    hass.config.units = METRIC_SYSTEM
    calls = []

    @callback
    def log_call(call):
        calls.append(call)
        eid = call.data.get("entity_id")
        if eid and call.service in (SERVICE_TURN_ON, SERVICE_TURN_OFF):
            new_st = "on" if call.service == SERVICE_TURN_ON else "off"
            cur_attrs = hass.states.get(eid).attributes if hass.states.get(eid) else {}
            hass.states.async_set(eid, new_st, cur_attrs)
        elif eid and call.service == "set_percentage":
            cur_attrs = hass.states.get(eid).attributes if hass.states.get(eid) else {}
            cur_attrs = dict(cur_attrs)
            cur_attrs["percentage"] = call.data.get("percentage")
            hass.states.async_set(eid, "on", cur_attrs)

    hass.services.async_register(HASS_DOMAIN, SERVICE_TURN_ON, log_call)
    hass.services.async_register(HASS_DOMAIN, SERVICE_TURN_OFF, log_call)
    hass.services.async_register("fan", "set_percentage", log_call)

    assert await async_setup_component(
        hass,
        input_boolean.DOMAIN,
        {"input_boolean": {"cooler_pct": None, "dummy_pct": None}},
    )
    assert await async_setup_component(
        hass,
        input_number.DOMAIN,
        {
            "input_number": {
                "temp_pct": {
                    "name": "test",
                    "initial": 10,
                    "min": 0,
                    "max": 40,
                    "step": 1,
                }
            }
        },
    )

    hass.states.async_set(
        "fan.test_pct_fan",
        "off",
        {
            "preset_modes": [],
            "percentage": 0,
            "percentage_step": 33.333333333333336,
        },
    )

    assert await async_setup_component(
        hass,
        CLIMATE,
        {
            "climate": {
                "platform": DOMAIN,
                "name": "test_pct",
                "heater": "input_boolean.dummy_pct",
                "cooler": "input_boolean.cooler_pct",
                "fan": "fan.test_pct_fan",
                "fan_on_with_ac": True,
                "target_sensor": common.ENT_SENSOR,
                "initial_hvac_mode": HVACMode.OFF,
                "fan_speed_stages": [
                    {"above": 0.3, "speed": "low"},
                    {"above": 3.0, "speed": "medium"},
                    {"above": 5.0, "speed": "high"},
                ],
            }
        },
    )
    await hass.async_block_till_done()

    # Turn on thermostat in COOL mode with target 20
    await common.async_set_hvac_mode(hass, HVACMode.COOL, "climate.test_pct")
    await common.async_set_temperature(hass, 20, "climate.test_pct")
    await hass.async_block_till_done()

    # Sensor at 24 -> delta is 4.0, which meets "medium" stage (above 3.0)
    calls.clear()
    setup_sensor(hass, 24)
    await hass.async_block_till_done()

    # Verify cooler turn_on service call
    cooler_turned_on = any(
        c.service == SERVICE_TURN_ON
        and c.data.get("entity_id") == "input_boolean.cooler_pct"
        for c in calls
    )
    assert cooler_turned_on, f"Cooler was not turned on. Calls: {calls}"

    # Verify fan turned on and staged percentage was applied
    fan_turned_on = any(
        c.service == SERVICE_TURN_ON and c.data.get("entity_id") == "fan.test_pct_fan"
        for c in calls
    )
    assert fan_turned_on, f"Fan was not turned on. Calls: {calls}"

    pct_calls = [
        c
        for c in calls
        if c.service == "set_percentage"
        and c.data.get("entity_id") == "fan.test_pct_fan"
    ]
    assert len(pct_calls) > 0, f"set_percentage was not called for fan. Calls: {calls}"
    assert (
        pct_calls[-1].data.get("percentage") == 66
    ), f"Expected 66% (medium), got {pct_calls[-1].data.get('percentage')}"

    # Now drop temperature to 19 -> goal reached
    calls.clear()
    setup_sensor(hass, 19)
    await hass.async_block_till_done()

    # Verify cooler is OFF
    assert hass.states.get("input_boolean.cooler_pct").state == "off"
    # Verify fan is turned off
    fan_turned_off = any(
        c.service == SERVICE_TURN_OFF and c.data.get("entity_id") == "fan.test_pct_fan"
        for c in calls
    )
    assert (
        fan_turned_off
    ), f"Fan was not turned off when cooling satisfied. Calls: {calls}"
