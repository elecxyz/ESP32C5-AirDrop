# ESP32-C5 technical guide

The C5 handles raw AWDL Wi-Fi frames; Linux still runs OWL, IPv6, TLS and the
patched OpenDrop receiver. This is not a standalone microcontroller AirDrop
implementation. See the [main README](../README.md) for the project overview.

## Current status

Tested on an ESP32-C5 revision 1.0 with 4 MB flash using ESP-IDF 5.5.5:

- iPhone discovery and the AirDrop upload handshake worked on channels 6 and 149.
- A live photo transfer completed on channel 149: 4.90 MiB in approximately
  8.5 seconds (0.58 MiB/s), extracted into Downloads, with the desktop
  notification confirmed. This is one measured result, not a speed guarantee.
- Live unplug/replug, automatic startup and full ten-minute expiry work;
  reboot testing is still pending.

Reliability across larger files, multiple files and other iOS versions still
needs testing. Hardware acknowledgements outside a normal Wi-Fi association
remain unverified.

## Build and flash

The setup helper targets CachyOS/Arch Linux. It installs dependencies, builds
the host bridge and creates this checkout's patched OpenDrop environment:

```sh
git clone --recurse-submodules https://github.com/elecxyz/ESP32C5-AirDrop.git
cd ESP32C5-AirDrop
./scripts/setup.sh
```

If already cloned, start from that checkout. The desktop integration requires
KDE Plasma 6; the receiver requires Linux user/network namespaces and access
to the board's serial device. Other host platforms need adaptation.

Use ESP-IDF 5.5.5, with its ESP32-C5 tools and Python environment activated:

```sh
cd c5/firmware
idf.py set-target esp32c5
idf.py build
idf.py -p /dev/ttyACM0 flash
cd ../..
```

Flashing replaces the board's application. This firmware does not join an
access point and contains no Wi-Fi credentials. After boot it waits for a USB
START command. Each radio session expires after ten minutes.

To rebuild just the Linux host after source changes, run from the repository
root with the OWL submodule present:

```sh
cmake -S c5/host -B c5/host/build -G Ninja
cmake --build c5/host/build
```

Dependencies: a C compiler, CMake, Ninja, pkg-config, libpcap, libev, libnl and
zlib. The setup helper installs these dependencies. The host build compiles
OWL directly from the pinned submodule.

## Desktop receive windows

After flashing, run `./scripts/install-desktop.sh --device /dev/ttyACM0` with
the board connected, replacing the port if needed.
The installer verifies the firmware and registers that particular board's
USB serial number locally in `/etc/esp32c5-airdrop/devices.json`. It does not
treat every device with Espressif's shared `303a:1001` USB ID as this receiver.
Exactly one physical board is registered; a different existing registration
is rejected, never silently expanded or replaced. Without `--device`, the
installer only probes when there is exactly one candidate. Normal hotplug
checks use the registered serial and do not probe unrelated ESP32s.

The registered board starts a ten-minute window on connection or desktop
startup. After expiry, the panel remains visible but idle; click it to start
another window. Unplugging stops reception and hides the panel entry.
Use `esp32c5-airdrop stop` before accessing the device manually, and
`esp32c5-airdrop start` to return to the desktop-managed receiver afterward.

Country and channel settings are copied from the user's config into
`/etc/esp32c5-airdrop/` during installation. Rerun the installer after changing
them. The board registry is local configuration, not part of the source repo.

Edit `~/.config/esp32c5-airdrop/config` before installation. Defaults are
`REG="00"` and `C5_CHANNEL=6`. For 5 GHz, set `REG` to the two-letter country
code for the board's actual location and choose a locally permitted channel:

```sh
REG="XX"        # Replace XX with your actual country code.
C5_CHANNEL=149  # Only where permitted.
```

`00` is the conservative world-domain default, not a country code. `XX` is a
placeholder. A country setting does not make a prohibited channel legal.

### Device name

The firmware's optional `device_name` response and Linux status/widget use
`CachyDrop-C5-Reciever`. The installer also assigns a Linux device alias. The
iPhone still sees **ESP32C5 AirDrop**.

The native USB descriptor is unchanged, so USB tools may still display the
original Espressif name.

## Manual receive

Stop the desktop receiver with `esp32c5-airdrop stop` first; only one process
can own the board's serial port.

Choose the correct two-letter country code for the radio's physical location.
For example, replace `XX` below with that code. Channel 149 is a useful 5 GHz
candidate when available in that country and advertised by the sending phone.
Channel 6 is the 2.4 GHz fallback.

```sh
c5/receive.sh -p /dev/ttyACM0 -c 149 -r XX
```

The launcher creates a private user/network namespace and runs for ten minutes.
It requires access to the serial port and Linux support for unprivileged user
and network namespaces. It does not require sudo on the tested system. No PC
Wi-Fi interface is disconnected or reconfigured. The temporary `awdlc5` TAP
interface exists only inside this namespace.

On the iPhone, open Share → AirDrop and select **ESP32C5 AirDrop**. Changing
the iPhone's receive visibility to **Everyone for 10 Minutes** is not required
in the tested setup. Successfully extracted files go directly to
`~/Downloads`. The notification hook inherited from CachyDrop runs on the
desktop side through a private local socket, including its **Show in Folder**
action.

Options are `-p PORT`, `-c CHANNEL`, `-r COUNTRY`, and `-s SECONDS` (10–600).
`ESP32C5_AIRDROP_RECEIVE_DIR` overrides Downloads; the historical
`CACHYDROP_C5_RECEIVE_DIR` is retained as a compatibility alias. Stop with Ctrl+C.

Runtime logs and the separate receiver identity are stored under
`${XDG_STATE_HOME:-~/.local/state}/esp32c5-airdrop/c5/`. The `current` symlink
points to the most recent session. Runtime logs may contain peer addresses,
device names and filenames. Keep them and received archives out of Git.
An incomplete `airdrop-received-*.dvzip` is retained for diagnosis and must not
trigger a completion notification.

The radio still has a hard ten-minute lifetime: a transfer started near the end
can be interrupted by that deadline. Start a fresh session before a long test.
The C5 completion validator currently supports the observed dvzip/ODC-cpio
variant, with regular files/directories and blocks up to 1 MiB. Other cpio
encodings and special-file entries are not supported by that validator.

## Diagnostics and transport

Stop the receiver before accessing its serial port with another program.
Using an environment with pyserial installed:

```sh
python c5/probe.py --info
python c5/probe.py --channels 6,149 --country XX --seconds 15 --tx-test
python -m unittest discover -s c5 -p 'test_*.py'
CACHYDROP_C5_HOST_TEST=1 .venv-opendrop/bin/python -m unittest discover -s c5 -p 'test_*.py'
```

The optional host integration test uses a pseudoterminal and a private network
namespace; it does not access the connected radio. It checks the compiled
host's handshake, fragmented replies, AWDL frame output and clean shutdown.
Using the OpenDrop virtual environment also runs receiver/extraction regression
tests for packet splits, checksums, truncation, unsafe paths and bounded
decompression.

The probe prints counts and signal levels, not captured device identifiers or
payloads. A successful TX command means the driver accepted a frame. Its TX
completion callback is a separate measurement; neither proves a successful
AirDrop transfer. Raw AWDL action and multicast data TX completed through the
SDK on channels 6, 44 and 149.

Firmware uses a 64 KiB variable-length RX buffer, retries temporary TX
allocation failures and filters Wi-Fi retries and unrelated unicast traffic.
The receiver limits the TCP receive buffer and initial window to avoid
exhausting the C5's memory.

When the sender omits an observed final HTTP chunk, the receiver validates
complete dvzip blocks and ODC-cpio entries before responding and closing the
connection. It rejects truncated archives; a timeout or trailer string inside
file contents is not enough. See [the patch guide](../patches/README.md).

Wire header: little-endian `<4sBBHI>`: `C5AW`, message type, version `1`, body
length, sequence ID. A little-endian CRC32 of header plus body follows the
body. Maximum body length is 4200 bytes. Commands are HELLO=1, START=2, STOP=3,
STATS=4, TX=5. Replies are ACK=128 (signed 32-bit ESP error), INFO=129 (JSON),
COUNTERS=130 (JSON), RX=131. START contains channel, two ASCII country letters,
and rate in Mbps (0/54, 24, 12 or 6). RX begins with `<BbHI>`: channel, RSSI,
reserved, device timestamp, followed by the 802.11 frame without FCS.

Only AWDL BSSID frames are forwarded over USB. TX is limited to AWDL action
and non-QoS data frames from the board's own MAC. The SDK's 1500-byte raw TX
limit is accommodated with an IPv6 MTU of 1280. The host currently uses a
fixed channel and OWL's `intersect` schedule strategy. USB transport latency,
radio acknowledgements, the peer's channel schedule and receive buffering
all affect speed; the requested PHY rate is not file-transfer throughput.

The native C5 USB connection uses Serial/JTAG, not a configurable USB Ethernet
device. The host backend is Linux-specific.

## Credits

New C5 firmware and Linux code is work by elecxyz, with OpenAI Codex as an
AI coding contributor, continued from CachyDrop. OWL's AWDL
implementation and the adapted TAP initialization retain their original
credits. ESP-IDF, its Wi-Fi libraries and the installed OpenDrop dependencies
retain their respective licences. See
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and [LICENSE](../LICENSE)
before distributing binaries.
