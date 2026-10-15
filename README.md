# ESP32C5 AirDrop

An experimental iPhone-to-Linux AirDrop receiver using an **ESP32-C5 as a USB
radio**. Files arrive in Downloads, with a KDE notification to open their folder.

This builds on [CachyDrop](https://github.com/elecxyz/cachydrop), originally
released in **August 2026** using a USB Wi-Fi adapter. The **October 2026 C5
work** continues its AWDL/OpenDrop integration and iOS compatibility fixes.
The original CachyDrop repository remains public; this one focuses on the C5.

> This is an experimental learning project. It works, but fricken barely.
> Expect rough edges, slow transfers and breakage. **Use at your own risk.**

## How it works

The C5 handles raw AWDL Wi-Fi frames over USB. Linux runs the host bridge,
OWL and patched OpenDrop receiver. It is **not a standalone ESP32 AirDrop
appliance**, and it leaves the computer's normal Wi-Fi connection alone.

The Linux/KDE integration registers one specific board. Connecting it starts
a ten-minute receive window; the panel button starts another after expiry.
Unplugging stops reception and hides the panel entry. Other ESP32s do not
automatically join the registration.

## Status

- A live iPhone photo transfer completed: **4.90 MiB in about 8.5 seconds**
  (approximately 0.58 MiB/s), saved to Downloads with a notification.
- Firmware and host builds pass. Live unplug/replug, automatic startup and
  full ten-minute expiry work; reboot testing is still pending.
- Send through the iPhone's Share → AirDrop sheet. Its receive visibility does
  not need to be set to **Everyone for 10 Minutes** in the tested setup.
- The ten-minute timeout is a hard stop, including during an upload.
- Larger transfers, multiple files and other iOS versions need more testing.

## Setup

The reference host is CachyOS/Arch Linux with KDE Plasma 6. You need an ESP32-C5
board with native USB Serial/JTAG, a data cable and ESP-IDF 5.5.5.

See the **[setup and technical guide](c5/README.md)** for building, flashing,
country/channel configuration, desktop installation and manual receiving.
Other host platforms need adaptation; they are not supported out of the box.

Keep received files, logs, local configuration, board identifiers and generated
keys out of commits and public diagnostics.

## Credits and licence

By **elecxyz**, with **OpenAI Codex as an AI coding contributor** to the firmware
and Linux implementation.

Continued from CachyDrop, whose foundations include Jed Blenkhorn's
[airdrop-mt7921](https://github.com/jedbillyb/airdrop-mt7921),
[OWL](https://github.com/seemoo-lab/owl) and
[OpenDrop](https://github.com/seemoo-lab/opendrop). Firmware uses Espressif's
ESP-IDF. Original authorship and licences are retained.

See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).
Independent project; not endorsed by Apple, Espressif or OpenAI.
