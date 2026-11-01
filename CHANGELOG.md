# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Temperature-based fan speed stages** - Automatically step the fan through speeds based on how far the room temperature is above target (#662)
  - New `fan_speed_stages` option: list of `{above, speed}` bands (e.g. low above 1.0, medium above 2.0, high above 3.0)
  - Stage speeds accept fan mode names, preset modes, climate fan modes, or percentages for percentage-based fans
  - Optional `fan_speed_stages_hysteresis` holds the current stage on the way down to prevent flapping; power on/off still respects `min_cycle_duration`
  - Optional `fan_speed_stages_min_interval` (default 5 minutes) limits how often the speed can change; manual picks and explicit target temperature changes always apply immediately
  - Below the lowest stage the fan turns off (or goes idle, following the current logic)
  - Applies in FAN_ONLY mode and whenever the fan runs in cooler mode (fan tolerance band or `fan_on_with_ac`)
- **Native Fan Speed Control** - Control fan speeds (low, medium, high, auto) directly from the thermostat interface, similar to built-in thermostats (#517)
  - Automatic detection of fan entity capabilities (preset_mode and percentage support)
  - Fan speed control works in FAN_ONLY mode, fan_on_with_ac mode, and fan tolerance mode
  - State persistence across Home Assistant restarts
  - Support for both preset_mode (named speeds) and percentage-based control
  - Automatic percentage-to-preset mapping for optimal compatibility
  - Full backward compatibility with switch-based fans (no fan speed control)

### Fixed

- YAML `fan_on_with_ac` now defaults to True for a separate fan (matching the config flow and models), so a YAML cooler+fan thermostat keeps the fan running alongside the cooler instead of staging it off when hottest; a fan sharing its cooler climate is still forced off
- Floor temperature limits (#647 groundwork): unset `min_floor_temp` no longer falls back to the 28 max default (which forced the heater on after an HVAC mode/preset change); a `0` floor limit in config or presets is now honoured instead of treated as unset; switching to a preset without floor limits (or to no preset) now resets limits to config instead of keeping the previous preset's

### Changed

- Fan entities now expose speed control capabilities when supported by the underlying fan entity
- FeatureManager enhanced to detect and track fan speed capabilities

### Documentation

- Added comprehensive fan speed control architecture documentation to CLAUDE.md
- Updated README.md with fan speed control usage examples and configuration guidance
- Added detailed fan speed control design and implementation documentation

## [v0.11.2] - 2025-01-XX

### Fixed

- Fixed heater/cooler turns off prematurely ignoring tolerance when active (#518) (#521)
- Corrected logger name handling for multiple thermostat instances (#511) (#513)
- Corrected inverted tolerance logic and added comprehensive behavioral tests (#506) (#507)

## [v0.11.0] - 2024-12-XX

See [RELEASE_NOTES_v0.11.0.md](RELEASE_NOTES_v0.11.0.md) for complete release notes.

### Major Features

- Complete UI Configuration - Set up your thermostat through Home Assistant's UI with guided wizard
- Template-Based Preset Temperatures - Dynamic presets using Home Assistant templates
- Input Boolean Support for Equipment - Use input_boolean entities for all equipment controls
- Docker-Based Development Environment - Professional development workflow for contributors

[Unreleased]: https://github.com/swingerman/ha-dual-smart-thermostat/compare/v0.11.2...HEAD
[v0.11.2]: https://github.com/swingerman/ha-dual-smart-thermostat/compare/v0.11.0...v0.11.2
[v0.11.0]: https://github.com/swingerman/ha-dual-smart-thermostat/releases/tag/v0.11.0
