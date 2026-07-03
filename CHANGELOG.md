# Changelog

## 2026-07-02

### Added
- Added a new staff dev log flow that lets staff choose a destination channel, fill out a form, and publish a polished embedded announcement.
- Added clearer playback diagnostics in hosting logs so maintainers can confirm which playback path was used.

### Improved
- Improved build performance by optimizing container layering and reducing build context size.
- Improved music reliability with stronger first-path retries when a track is still finishing preparation.
- Improved music request experience with a single progress embed that updates through request stages and then becomes the final now playing card.
- Improved now playing and queue messaging to be cleaner and more consistent for users.
- Improved status reporting with richer runtime and service health information for faster troubleshooting.

### Changed
- Updated public command messaging to avoid exposing provider-specific playback details.
- Updated lyrics and playback presentation to keep output user-focused and concise.

### Fixed
- Fixed issues where track lookup results could be misread in certain response shapes.
- Fixed a logging key issue in music command flow.
- Fixed startup configuration conflicts that could prevent the audio service from initializing.
