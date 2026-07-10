# Changelog

All notable changes to this egg are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
releases are dated with [Calendar Versioning](https://calver.org/) (`YYYY.MM.DD`).

## [Unreleased]

## [2026.07.10] - 2026-07-10

### Added

- A `SERVER_WHLFILE` variable for running a local `.whl` instead of pulling from
  PyPI — handy for a development build or a version that never shipped to PyPI.
  Point it at the uploaded file or a direct URL; when set, it wins over the
  version field.

### Changed

- Leave the version field blank to always run the latest release. To pin a
  release, just type the number (e.g. `0.11.5`) — the old `endstone==` prefix is
  gone.
- Refreshed `server.properties` to match the current Bedrock defaults: the new
  `transport` toggle, UDP port mapping, the split `server-authoritative-*-strict`
  movement flags, and the extra content-logging options.
- Installs now run in the slim Alpine installer image instead of a full Python
  image, so setting up a server is quicker.

### Removed

- Dropped Python 3.10. The egg now offers Python 3.11 through 3.14.

### Fixed

- Servers failed to start because the container entrypoint mangled the braces and
  quotes in the startup command. It now hands `pip` a clean package name every
  time.

## [2026.03.23] - 2026-03-23

### Added

- `validate.py`, which checks the egg against Pterodactyl's import rules before
  you ship it.

### Changed

- Reworked the repository and its docs so hosting providers can pick up the egg
  more easily.

[unreleased]: https://github.com/EndstoneMC/pterodactyl/compare/2026.07.10...HEAD
[2026.07.10]: https://github.com/EndstoneMC/pterodactyl/compare/2026.03.23...2026.07.10
[2026.03.23]: https://github.com/EndstoneMC/pterodactyl/releases/tag/2026.03.23
