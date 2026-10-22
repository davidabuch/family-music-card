# Family Music Card

A Home Assistant custom integration with a bundled Lovelace card for a family-friendly Music Assistant + Sonos experience.

This repository is the durable source of truth for the Family Music controller.

## Goals

- One shared destination selector for individual Sonos rooms and permanent Music Assistant groups.
- Full-catalog Apple Music and Spotify search through Music Assistant.
- Artist → album → track browsing.
- Attractive now-playing artwork and playback/volume controls.
- Family-friendly operation without AirPlay or switching between multiple apps/cards.
- No Music Assistant access token stored in Lovelace configuration.

Development follows the HomeKit Integrations autonomous GitHub + Home Assistant engineering workflow.

## Installation status

The project is packaged as a HACS custom repository integration. Release installation and Home Assistant commissioning are performed only after Hassfest, HACS validation, lint, Python compilation, and card syntax checks are green.

## Grouped playback and temporary-group cleanup

See [Issue #98 investigation and commissioning notes](docs/issue-98-investigation.md)
for the confirmed standalone origin failure, HA-card diagnostics, conservative
backend ownership policy, restart behavior and physical acceptance checks.

See [Post-PR #99 state synchronization](docs/post-99-state-synchronization.md) for
player/queue observation fixes, measured Pico propagation, diagnostics and commissioning.
