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
    control = _control(
        _staged_config(), modes=["low", "medium", "high"], selected=None
    )
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
