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
    assert (
        FeatureManager._normalize_fan_interval({"minutes": 2}) == 120.0
    )
    assert FeatureManager._normalize_fan_interval("bogus") == 300.0
