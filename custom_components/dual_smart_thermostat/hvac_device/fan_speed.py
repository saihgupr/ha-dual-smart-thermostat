"""Fan speed control for the one entity that owns the thermostat's fan_mode."""

from __future__ import annotations

import logging
from time import monotonic
from typing import Any

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.components.fan import (
    ATTR_PERCENTAGE,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    DOMAIN as FAN_DOMAIN,
    SERVICE_SET_PERCENTAGE,
    SERVICE_SET_PRESET_MODE,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, State, callback

from ..climate_roles import is_climate_entity
from ..const import FAN_MODE_TO_PERCENTAGE, PERCENTAGE_TO_FAN_MODE
from .climate_child import peek_climate_child

_LOGGER = logging.getLogger(__name__)


class FanSpeedControl:
    """Detect, hold, restore and apply a fan speed on one entity."""

    def __init__(self, hass: HomeAssistant, entity_id: str, owner: Any = None) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.owner = owner  # the thermostat whose fan speed this is
        self._is_climate = is_climate_entity(entity_id)
        self.fan_modes: list[str] = []
        self.uses_preset_modes = False
        self.current_fan_mode: str | None = None
        self._pending_fan_mode: str | None = None
        # The user's selection: "auto" means temperature stages decide.
        # Staging applies speeds without touching this, so a manual fixed
        # speed sticks (and persists) until the user picks another mode.
        self.selected_fan_mode: str | None = None
        self._detect_fan_capabilities()
        if "auto" in self.fan_modes:
            self.selected_fan_mode = "auto"
        # Last automatic or manual speed change (monotonic seconds), for the
        # minimum interval between speed changes.
        self._last_speed_change: float | None = None

    def _detect_fan_capabilities(self, fan_state: State | None = None) -> None:
        """Detect if fan entity supports speed control."""
        fan_state = fan_state or self.hass.states.get(self.entity_id)

        if not fan_state:
            _LOGGER.debug("Fan entity %s not found, no speed control", self.entity_id)
            return

        # Check domain - only "fan" domain supports speed control
        entity_domain = fan_state.domain
        if entity_domain == SWITCH_DOMAIN:
            _LOGGER.debug("Fan entity %s is a switch, no speed control", self.entity_id)
            return

        if entity_domain == FAN_DOMAIN:
            # Check for preset_mode support
            preset_modes = fan_state.attributes.get(ATTR_PRESET_MODES)
            if preset_modes:
                self.fan_modes = list(preset_modes)
                self.uses_preset_modes = True
                _LOGGER.info(
                    "Fan entity %s supports preset modes: %s",
                    self.entity_id,
                    self.fan_modes,
                )
                # Set initial mode from entity state
                current_preset = fan_state.attributes.get(ATTR_PRESET_MODE)
                if current_preset:
                    self.current_fan_mode = current_preset
                return

            # Check for percentage support
            percentage = fan_state.attributes.get(ATTR_PERCENTAGE)
            if percentage is not None:
                self.fan_modes = ["auto", "low", "medium", "high"]
                self.uses_preset_modes = False
                _LOGGER.info(
                    "Fan entity %s supports percentage-based speed control",
                    self.entity_id,
                )
                # Default to auto mode for percentage-based control
                self.current_fan_mode = "auto"
                return

        if entity_domain == CLIMATE_DOMAIN:
            fan_modes = fan_state.attributes.get(ATTR_FAN_MODES)
            if fan_modes:
                self.fan_modes = list(fan_modes)  # verbatim: quiet / strong survive
                self.uses_preset_modes = True
                self.current_fan_mode = fan_state.attributes.get(ATTR_FAN_MODE)
                _LOGGER.info(
                    "Climate entity %s supports fan modes: %s",
                    self.entity_id,
                    self.fan_modes,
                )
                return

        _LOGGER.debug("Fan entity %s does not support speed control", self.entity_id)

    @callback
    def redetect_capabilities(self, entity_id: str, new_state: State) -> bool:
        """Retry detection once our entity shows up, True if it gained speed control."""
        if entity_id != self.entity_id or self.supports_fan_mode:
            return False

        self._detect_fan_capabilities(new_state)

        if not self.supports_fan_mode:
            return False

        if self._pending_fan_mode is not None:
            self.restore_fan_mode(self._pending_fan_mode)
            self._pending_fan_mode = None

        return True

    async def async_reapply(self, entity_id: str) -> None:
        """Re-send the selected speed after the climate child was turned on.

        Many ACs (infrared ones especially) reset their fan speed on a mode
        change. Acts only when this control targets that very climate entity,
        so a separate fan entity, or another climate, is never touched.
        """
        if (
            entity_id != self.entity_id
            or not self.supports_fan_mode
            or self.current_fan_mode is None
        ):
            return
        await self.async_set_fan_mode(self.current_fan_mode, user_selected=False)

    @property
    def supports_fan_mode(self) -> bool:
        """Return if fan supports speed control."""
        return bool(self.fan_modes)

    def restore_fan_mode(self, fan_mode: str) -> None:
        """Restore fan mode from persisted state.

        Args:
            fan_mode: The fan mode to restore

        This method validates that the fan mode is valid for the current
        fan device before restoring it. Invalid modes are logged and ignored.
        A restored mode counts as the user's selection.
        """
        if not self.supports_fan_mode:
            # Entity isn't up yet; hold it for redetect_capabilities (issue #636).
            self._pending_fan_mode = fan_mode
            return

        if fan_mode in self.fan_modes:
            self.current_fan_mode = fan_mode
            self.selected_fan_mode = fan_mode
            _LOGGER.info("Restored fan mode %s for entity %s", fan_mode, self.entity_id)
        else:
            _LOGGER.warning(
                "Cannot restore invalid fan mode %s for entity %s. Available modes: %s",
                fan_mode,
                self.entity_id,
                self.fan_modes,
            )

    def should_auto_stage(self) -> bool:
        """Whether temperature stages may pick the speed right now.

        True when stages are configured, the fan supports speeds, and the
        user selected "auto" (or never selected a fixed speed on a fan
        without an auto mode). A manual low/medium/high selection sticks.
        """
        if not self.supports_fan_mode or not self._stages():
            return False
        if "auto" in self.fan_modes:
            return self.selected_fan_mode == "auto"
        return self.selected_fan_mode is None

    async def async_set_fan_mode(
        self, fan_mode: str, user_selected: bool = True
    ) -> None:
        """Set the fan speed mode.

        Args:
            fan_mode: The fan mode to set.
            user_selected: True when the user picked it (manual selection or
                restore). Automatic staging passes False so the applied speed
                never overwrites the user's selection.
        """
        if not self.supports_fan_mode:
            _LOGGER.warning(
                "Fan entity %s does not support speed control", self.entity_id
            )
            return

        if fan_mode not in self.fan_modes:
            _LOGGER.warning(
                "Invalid fan mode %s for entity %s. Available modes: %s",
                fan_mode,
                self.entity_id,
                self.fan_modes,
            )
            return

        _LOGGER.debug("Setting fan mode to %s for entity %s", fan_mode, self.entity_id)

        previous_fan_mode = self.current_fan_mode

        if self._is_climate:
            # The shared child decides whether the AC is running (and sends it);
            # otherwise the selection is only recorded below for the next turn on.
            if child := peek_climate_child(self.hass, self.entity_id):
                await child.async_set_fan_mode(fan_mode, None, self.owner)
        elif self.uses_preset_modes:
            # Use preset_mode service
            await self.hass.services.async_call(
                FAN_DOMAIN,
                SERVICE_SET_PRESET_MODE,
                {ATTR_ENTITY_ID: self.entity_id, ATTR_PRESET_MODE: fan_mode},
                blocking=True,
            )
        else:
            # Use percentage service
            percentage = FAN_MODE_TO_PERCENTAGE.get(fan_mode)
            if percentage is None:
                _LOGGER.error("No percentage mapping for fan mode %s", fan_mode)
                return

            await self.hass.services.async_call(
                FAN_DOMAIN,
                SERVICE_SET_PERCENTAGE,
                {ATTR_ENTITY_ID: self.entity_id, ATTR_PERCENTAGE: percentage},
                blocking=True,
            )

        self.current_fan_mode = fan_mode
        if user_selected:
            self.selected_fan_mode = fan_mode
        if previous_fan_mode != fan_mode:
            self._last_speed_change = monotonic()
        _LOGGER.info("Fan mode set to %s for entity %s", fan_mode, self.entity_id)

    def _stages(self) -> list[dict] | None:
        """Return configured speed stages from the owner features, if any."""
        owner = self.owner
        stages = getattr(owner, "fan_speed_stages", None)
        return stages or None

    def _hysteresis(self) -> float:
        owner = self.owner
        try:
            return float(getattr(owner, "fan_speed_stages_hysteresis", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _min_interval(self) -> float:
        owner = self.owner
        try:
            return float(
                getattr(owner, "fan_speed_stages_min_interval", 300.0) or 0.0
            )
        except (TypeError, ValueError):
            return 300.0

    def stage_change_allowed(self) -> bool:
        """Whether an automatic speed change may run right now.

        Manual picks always apply immediately (and restart the clock).
        """
        interval = self._min_interval()
        if interval <= 0 or self._last_speed_change is None:
            return True
        return (monotonic() - self._last_speed_change) >= interval

    def normalize_stage_speed(self, raw: Any) -> str | None:
        """Map a stage `speed` value (name or percentage) to a valid fan mode."""
        if raw is None:
            return None
        # Numeric string -> number
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None
            try:
                number = int(float(text))
                # "33" / "33.0" behave like 33
                if str(number) == text or float(text) == number:
                    raw = number
                else:
                    raw = text
            except ValueError:
                raw = text
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            pct = int(raw)
            if self.uses_preset_modes or self._is_climate:
                _LOGGER.warning(
                    "Numeric fan stage speed %s ignored for preset/climate fan %s",
                    raw,
                    self.entity_id,
                )
                return None
            if pct in PERCENTAGE_TO_FAN_MODE:
                mode = PERCENTAGE_TO_FAN_MODE[pct]
                return mode if mode in self.fan_modes else None
            # Snap 0-100 to low/medium/high thirds for 3-speed fans.
            if pct <= 0:
                return None
            if pct <= 33:
                mode = "low"
            elif pct <= 66:
                mode = "medium"
            else:
                mode = "high"
            # "auto" also maps to 100% but stages should pick a real speed.
            if mode not in self.fan_modes:
                # Fall back to closest available mode by percentage value.
                available = [
                    (FAN_MODE_TO_PERCENTAGE.get(m, 100), m) for m in self.fan_modes
                ]
                if not available:
                    return None
                available.sort()
                mode = min(available, key=lambda kv: abs(kv[0] - pct))[1]
            return mode
        if isinstance(raw, str):
            if raw in self.fan_modes:
                return raw
            lowered = raw.lower()
            for mode in self.fan_modes:
                if mode.lower() == lowered:
                    return mode
            _LOGGER.warning(
                "Invalid fan stage speed %s for entity %s. Available: %s",
                raw,
                self.entity_id,
                self.fan_modes,
            )
            return None
        return None

    def resolve_staged_speed(self, delta: float | None) -> str | None:
        """Pick the fan speed for a temperature delta above target.

        `delta` is cur - target. Returns a valid fan mode or None when below
        the lowest stage (fan should be off/idle). Applies hysteresis on step
        down so the speed does not flap around a threshold.
        """
        stages = self._stages()
        if not stages or delta is None:
            return None if stages else None
        # Highest stage whose threshold is met.
        target: str | None = None
        target_idx = -1
        for idx, stage in enumerate(stages):
            mode = self.normalize_stage_speed(stage.get("speed"))
            if mode is None:
                continue
            try:
                above = float(stage.get("above", 0))
            except (TypeError, ValueError):
                continue
            if delta >= above:
                target = mode
                target_idx = idx
        hysteresis = self._hysteresis()
        if hysteresis <= 0 or self.current_fan_mode is None:
            return target
        # Find where the current speed sits in the stage table.
        current_idx = -1
        current_above: float | None = None
        for idx, stage in enumerate(stages):
            if self.normalize_stage_speed(stage.get("speed")) == self.current_fan_mode:
                current_idx = idx
                try:
                    current_above = float(stage.get("above", 0))
                except (TypeError, ValueError):
                    current_above = None
        if current_idx < 0 or current_above is None:
            return target
        if target_idx < current_idx:
            # Step down (or off): hold current until clearly below its band.
            if delta >= current_above - hysteresis:
                return self.current_fan_mode
        return target
