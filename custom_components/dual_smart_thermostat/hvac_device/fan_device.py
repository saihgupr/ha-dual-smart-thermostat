from datetime import timedelta
import logging

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.core import HomeAssistant

from ..hvac_device.cooler_device import CoolerDevice
from ..hvac_device.fan_speed import FanSpeedControl
from ..managers.environment_manager import EnvironmentManager
from ..managers.feature_manager import FeatureManager
from ..managers.hvac_power_manager import HvacPowerManager
from ..managers.opening_manager import OpeningManager

_LOGGER = logging.getLogger(__name__)


class FanDevice(CoolerDevice):

    hvac_modes = [HVACMode.FAN_ONLY, HVACMode.OFF]
    fan_air_surce_outside = False
    pushes_setpoint = False

    def role_modes(self) -> tuple[HVACMode, ...]:
        return (HVACMode.FAN_ONLY,)

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        min_cycle_duration: timedelta,
        initial_hvac_mode: HVACMode,
        environment: EnvironmentManager,
        openings: OpeningManager,
        features: FeatureManager,
        hvac_power: HvacPowerManager,
    ) -> None:
        super().__init__(
            hass,
            entity_id,
            min_cycle_duration,
            initial_hvac_mode,
            environment,
            openings,
            features,
            hvac_power,
        )

        if self.features.is_fan_uses_outside_air:
            self.fan_air_surce_outside = True

        # Fan speed control capabilities (detected on construction)
        self.speed = FanSpeedControl(hass, entity_id, features)

    def fan_speed(self) -> FanSpeedControl:
        return self.speed

    def _staged_delta(self) -> float | None:
        """Temperature above target driving fan stages, or None if unknown."""
        cur = self.environment.cur_temp
        try:
            target = getattr(self.environment, self.target_env_attr)
        except AttributeError:
            target = None
        if cur is None or target is None:
            return None
        return cur - target

    async def async_control_hvac(self, time=None, force=False):
        """Run on/off control, then apply temperature-based speed stages."""
        if not self.speed.should_auto_stage():
            # No stages, no speed support, or the user picked a fixed speed:
            # plain on/off control at the selected speed.
            await super().async_control_hvac(time, force)
            return
        if self._hvac_mode == HVACMode.OFF:
            await super().async_control_hvac(time, force)
            return

        staged = self.speed.resolve_staged_speed(self._staged_delta())

        await super().async_control_hvac(time, force)

        # Openings win over stages: super() already stopped the fan.
        if self.openings.any_opening_open(self._hvac_mode or HVACMode.FAN_ONLY):
            return

        if staged is None:
            if self.is_active:
                if self.hvac_controller.min_cycle_elapsed(force):
                    await self.async_turn_off()
                else:
                    _LOGGER.debug(
                        "Holding fan %s on: min cycle before staged stop",
                        self.entity_id,
                    )
            return

        if not self.is_active:
            if not self.hvac_controller.min_cycle_elapsed(force):
                _LOGGER.debug(
                    "Holding fan %s off: min cycle before staged start",
                    self.entity_id,
                )
                return
            await self.async_turn_on()
        if staged != self.speed.current_fan_mode:
            await self.speed.async_set_fan_mode(staged, user_selected=False)
            if getattr(self, "_async_write_ha_state_cb", None) is not None:
                self._async_write_ha_state_cb()

    @property
    def hvac_action(self) -> HVACAction:
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self.is_active:
            return HVACAction.FAN
        return HVACAction.IDLE
