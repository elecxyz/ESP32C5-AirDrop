# OpenDrop receiver compatibility patches

ESP32C5 AirDrop builds a private OpenDrop 0.13.0 environment and applies these
patches, inherited from [CachyDrop](https://github.com/elecxyz/cachydrop).
`scripts/setup.sh` reapplies them when recreating the environment and defines
their application order.

These patches modify GPL-3.0-covered OpenDrop source and are distributed under
GPL-3.0. See [the third-party notices](../THIRD_PARTY_NOTICES.md) for upstream
authorship and provenance, and [LICENSE](../LICENSE) for the full licence.

## Included patches

### `opendrop-ios26-airdrop.patch`

Handles chunked `/Discover` and `/Ask` bodies, accepts the
`application/x-dvzip` format observed with iOS 26, decodes dvzip to ODC cpio
and buffers uploads to disk before extraction.

### `opendrop-recv-window.patch`

Delays `100 Continue` until the receiver is ready to consume the upload. It
also carries the original large socket-buffer setting from CachyDrop.
**The C5 receiver overrides that setting with a smaller buffer and initial
window**: a burst suited to a desktop Wi-Fi adapter can exhaust the C5's
limited memory. C5-specific tuning lives in `c5/receiver.py` and the launcher.

### `opendrop-py314-send.patch`

Updates removed Python, Pillow and libarchive APIs: modern SSL context
construction, `Image.Resampling.LANCZOS` and current `ArchiveEntry` ownership.
Despite the patch name, this project supports receiving only.

### `opendrop-ask-confirm.patch`

Adds an optional confirmation hook for incoming transfers. This project's
desktop receiver accepts transfers while its receive window is active.

### `opendrop-mdns-reannounce.patch`

Re-announces the `_airdrop._tcp` service periodically without restarting the
HTTPS server. Repetition helps when multicast frames miss the short periods
in which AWDL peers share a channel.

### `opendrop-chunk-reader.patch`

Reads each declared HTTP chunk completely before consuming its terminator.
A short socket read is not a complete chunk. The C5 specialization adds
archive-completion validation; it does not interpret a timeout or truncated
chunk as a successful transfer.

### `opendrop-received-hook.patch`

Runs `AIRDROP_RECEIVED` only after successful archive receipt and extraction.
The hook receives the extraction directory as its first argument and a
NUL-delimited file list on standard input. The C5 notification relay delivers
that result to the desktop side for file tidying and the KDE notification.

### `opendrop-stable-receiver.patch`

Persists a stable Bonjour service identifier in the receiver's configured
identity directory, adds a hostname option and handles SIGTERM with orderly
shutdown. The C5 receiver uses its own identity under
`~/.local/state/esp32c5-airdrop/c5/`, not the old CachyDrop identity. Stable
identity and Bonjour goodbye records help avoid stale iPhone list entries.

## C5 specialization and verification

The extra upload-completion logic is in `c5/receiver.py` and
`c5/archive_end.py`. It verifies dvzip blocks, all declared file bytes and the
actual ODC-cpio trailer before responding when the sender omits an observed
final HTTP chunk. A matching string inside file contents is not sufficient.
Truncated and unsafe archives are rejected; see the synthetic regression
fixtures in `c5/test_*.py`.

Rebuild and run the tests from the repository root:

```sh
./scripts/setup.sh
.venv-opendrop/bin/python -m unittest discover -s c5 -p 'test_*.py'
```
