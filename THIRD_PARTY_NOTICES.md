# Third-party notices

This file records the provenance of code that ESP32C5 AirDrop adapts, patches
or includes by reference.

## ESP32C5 AirDrop and CachyDrop

ESP32C5 AirDrop is the C5-focused continuation of
[CachyDrop](https://github.com/elecxyz/cachydrop), by **elecxyz**. CachyDrop's
initial public release dates to August 2026, and its C5 proof of transfer to
October 2026.

New radio firmware, USB transport, receiver adaptations, tests, diagnostics,
desktop integration and documentation are work by elecxyz, 2026, distributed
under GPL-3.0; see individual SPDX headers and the repository's `LICENSE`.
OpenAI Codex is credited as an AI coding contributor to both the C5 firmware
and Linux-side implementation.
Receiver compatibility patches and desktop/file-handling components were
carried forward from CachyDrop.

## airdrop-mt7921

- Project: [airdrop-mt7921](https://github.com/jedbillyb/airdrop-mt7921)
- Author: Jed Blenkhorn (`jedbillyb`)
- Licence: GNU General Public License version 3
- Provenance: starting point for CachyDrop's experimental scripts and part of
  its OpenDrop compatibility work, some of which continues here

## OWL

- Included fork: [jedbillyb/owl](https://github.com/jedbillyb/owl)
- Upstream project: [seemoo-lab/owl](https://github.com/seemoo-lab/owl)
- Principal upstream credit: the Open Wireless Link Project and Milan Stute
- Downstream fork changes: Jed Blenkhorn
- Licence: GNU General Public License version 3; many source headers permit
  version 3 or any later version
- Use here: AWDL link-layer implementation, included as the `owl/` Git
  submodule at a pinned commit and compiled into the C5 host bridge

TAP initialization in `c5/host/transport.c` is adapted from OWL's
`daemon/io.c` and retains credit to the Open Wireless Link Project and
Milan Stute.

The OWL checkout retains its copyright headers and `COPYING` file. It also
retains notices for code contained within OWL, including GoogleTest's BSD
3-Clause licence, the radiotap library's ISC-style licence, and the MIT notice
in `src/log.c`. Those files are authoritative for the submodule's contents.

## OpenDrop

- Project: [seemoo-lab/opendrop](https://github.com/seemoo-lab/opendrop)
- Authors credited upstream: Milan Stute and Alexander Heinrich
- Licence: GNU General Public License version 3
- Version patched: OpenDrop 0.13.0
- Use here: AirDrop service advertisement, HTTPS protocol and archive handling

The files under `patches/opendrop-*.patch` modify GPL-covered OpenDrop source
and are distributed under GPL-3.0 consistently with OpenDrop and the
repository-level `LICENSE`. The setup script downloads OpenDrop 0.13.0,
applies these inherited compatibility patches locally and preserves its
licence inside the generated Python environment. The C5 receiver additionally
specializes buffering and validates complete dvzip/ODC-cpio uploads.

## Espressif ESP-IDF

The C5 firmware builds against
[Espressif ESP-IDF](https://github.com/espressif/esp-idf), tested with
version 5.5.5. ESP-IDF is primarily Apache-2.0 and includes separately
licensed components and binary Wi-Fi libraries. The SDK and those libraries
are supplied externally, not copied into this repository.

Consult the exact SDK's component and binary licences before distributing
compiled firmware. This source-level notice does not establish licence
compatibility for a combined binary or replace a review of its terms.

## Installed dependencies

OpenDrop declares Pillow, fleep, ifaddr, libarchive-c, requests,
requests-toolbelt and python-zeroconf as Python dependencies. They are fetched
by `pip`; their source is not stored here. In particular:

- [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) is
  LGPL-2.1-or-later.
- [python-libarchive-c](https://github.com/Changaco/python-libarchive-c) is
  dedicated under CC0-1.0 and dynamically accesses the separately installed
  libarchive library.
- [pyserial](https://github.com/pyserial/pyserial) is used for C5 diagnostics
  and board identification; its licence is retained in the installed package.

System packages such as KDE Plasma, systemd, libarchive, libev, libnl,
libpcap and zlib are installed separately, not redistributed in this source
tree.

If this project is distributed as a binary, firmware image, system image,
package containing the Python environment or other self-contained bundle,
the distributor must audit that exact bundle and reproduce the licences and
notices required by its dependency versions. This source notice is not a
substitute for a binary-distribution licence audit.

## Trademarks and independence

Apple, AirDrop, iPhone, iOS and macOS are trademarks of Apple Inc. ESP32C5
AirDrop is an independent compatibility project, not affiliated with,
sponsored by or endorsed by Apple, Espressif or any upstream project above.
