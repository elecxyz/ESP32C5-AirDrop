#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail

device_args=()
if [[ $# == 2 && "$1" == --device && "$2" =~ ^/dev/(ttyACM[0-9]+)$ ]]; then
  device_args=(--interface "${BASH_REMATCH[1]}")
elif [[ $# != 0 ]]; then
  echo "Usage: ./scripts/install-desktop.sh [--device /dev/ttyACM<number>]" >&2
  exit 2
fi

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DESKTOP="$ROOT/desktop"
INSTALL_USER=$(id -un)
INSTALL_GROUP=$(id -gn)
INSTALL_UID=$(id -u)
[[ "$INSTALL_UID" != 0 ]] || { echo "Run this as your Plasma desktop user, not with sudo." >&2; exit 1; }

for tool in python3 sudo systemctl udevadm kpackagetool6 qdbus6 notify-send dolphin getent; do
  command -v "$tool" >/dev/null || { echo "Missing $tool; install the desktop prerequisites first." >&2; exit 1; }
done
getent group uucp >/dev/null || { echo "This CachyOS installer needs the uucp serial-device group." >&2; exit 1; }
for required in "$ROOT/c5/host/build/cachydrop-c5" "$ROOT/.venv-opendrop/bin/python"; do
  [[ -x "$required" ]] || { echo "Receiver is not built; run ./scripts/setup.sh first." >&2; exit 1; }
done
for source in c5_device.py esp32c5-airdrop-session esp32c5-airdrop-control \
  esp32c5-airdrop@.service.in 49-esp32c5-airdrop-polkit.rules add-widget.js plasmoid/metadata.json; do
  [[ -r "$DESKTOP/$source" ]] || { echo "Incomplete desktop source checkout." >&2; exit 1; }
done
"$ROOT/.venv-opendrop/bin/python" -c 'import opendrop, serial'
qdbus6 org.kde.plasmashell /PlasmaShell >/dev/null

umask 077
temporary=$(mktemp -d)
trap 'rm -rf -- "$temporary"' EXIT
if ! python3 "$DESKTOP/c5_device.py" discover --verify "${device_args[@]}" --output "$temporary/verified.json"; then
  echo "Connect the intended flashed C5 and stop any running receiver. If several ESP boards are connected, select this board with --device /dev/ttyACM<number>. Your user needs serial-device access." >&2
  exit 1
fi

# Generate everything before making privileged changes. Neither the local
# configuration nor USB identity is evaluated as shell code or printed.
python3 - "$temporary" "$ROOT" "$INSTALL_USER" "$INSTALL_GROUP" "$HOME" "$INSTALL_UID" \
  "${XDG_CONFIG_HOME:-$HOME/.config}/esp32c5-airdrop/config" \
  /etc/esp32c5-airdrop/devices.json <<'PY'
import json
import os
from pathlib import Path
import re
import shlex
import sys

temporary, project, user, group, home, uid, config, registry = sys.argv[1:]
temporary = Path(temporary)
class RegistrationMismatch(ValueError):
    pass

try:
    values = {"REG": "00", "C5_CHANNEL": "6"}
    if Path(config).exists():
        for line in Path(config).read_text().splitlines():
            fields = shlex.split(line, comments=True)
            if not fields:
                continue
            if len(fields) != 1 or "=" not in fields[0]:
                raise ValueError("Use only REG and C5_CHANNEL assignments in local configuration")
            key, value = fields[0].split("=", 1)
            if key not in values:
                raise ValueError("Unknown local configuration key")
            values[key] = value
    values.update({key: os.environ[key] for key in values if key in os.environ})
    values["REG"] = values["REG"].upper()
    if not re.fullmatch(r"00|[A-Z]{2}", values["REG"], re.ASCII):
        raise ValueError("REG must be 00 or a two-letter country code")
    if values["C5_CHANNEL"] not in {"6", "36", "44", "149"}:
        raise ValueError("C5_CHANNEL must be 6, 36, 44, or 149")
    if values["REG"] == "00" and values["C5_CHANNEL"] != "6":
        raise ValueError("Set your actual country code before choosing a 5 GHz channel")

    serial_re = re.compile(r"[A-Za-z0-9:_-]{1,128}\Z", re.ASCII)
    devices = json.loads((temporary / "verified.json").read_text())["devices"]
    if not isinstance(devices, list) or len(devices) != 1:
        raise ValueError("Exactly one verified C5 device is required")
    for device in devices:
        if (not isinstance(device, dict) or
                not re.fullmatch(r"ttyACM[0-9]+", device.get("interface", ""), re.ASCII) or
                not serial_re.fullmatch(device.get("serial", "")) or
                not device.get("firmware", "").startswith("cachydrop-c5-radio-")):
            raise ValueError("Invalid verified C5 device data")
    serials = []
    if Path(registry).exists():
        serials = json.loads(Path(registry).read_text())["serials"]
        if not isinstance(serials, list) or not all(
                isinstance(value, str) and serial_re.fullmatch(value) for value in serials):
            raise ValueError("Existing device registration is invalid; it was not overwritten")
    selected_serial = devices[0]["serial"]
    if serials not in ([], [selected_serial]):
        raise RegistrationMismatch()
    serials = [selected_serial]
    (temporary / "devices.json").write_text(json.dumps({"serials": serials}) + "\n")
    (temporary / "interfaces").write_text("\n".join(sorted({d["interface"] for d in devices})) + "\n")
    rules = ["# Locally registered C5 receivers; never publish this file."]
    rules.append('ACTION=="add", SUBSYSTEM=="tty", KERNEL=="ttyACM[0-9]*", '
                 'ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", '
                 f'ATTRS{{serial}}=="{selected_serial}", TAG+="systemd", TAG+="uaccess", '
                 'ENV{ID_MODEL}="CachyDrop-C5-Reciever", '
                 'ENV{ID_MODEL_FROM_DATABASE}="CachyDrop-C5-Reciever", '
                 'ENV{SYSTEMD_WANTS}+="esp32c5-airdrop@%k.service", '
                 'SYMLINK+="CachyDrop-C5-Reciever"')
    (temporary / "70-esp32c5-airdrop.rules").write_text("\n".join(rules) + "\n")
    for name, key in (("regdomain", "REG"), ("channel", "C5_CHANNEL")):
        (temporary / name).write_text(values[key] + "\n")

    if not uid.isascii() or not uid.isdigit():
        raise ValueError("Invalid user ID")
    for name in (user, group):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*\$?", name, re.ASCII):
            raise ValueError("Unsupported local user or group name")
    for path in (home, project):
        if not path.startswith("/") or any(ord(char) < 32 for char in path):
            raise ValueError("Unsupported installation path")
    def unit_escape(value):
        return value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    template = (Path(project) / "desktop/esp32c5-airdrop@.service.in").read_text()
    for key, value in {"USER": user, "GROUP": group, "HOME": home, "PROJECT": project, "UID": uid}.items():
        template = template.replace(f"@{key}@", unit_escape(value))
    # Quote the complete KEY=value assignment so spaces in the checkout work.
    template = "\n".join('Environment="' + line[len("Environment="):] + '"'
                         if line.startswith("Environment=") else line
                         for line in template.splitlines()) + "\n"
    (temporary / "esp32c5-airdrop@.service").write_text(template)
except RegistrationMismatch:
    print("A different board or multiple boards are already registered. Refusing to replace that registration or add another device; no system files changed.", file=sys.stderr)
    raise SystemExit(1)
except (OSError, ValueError, KeyError, TypeError, AttributeError):
    print("Invalid installer configuration or registration; no system files changed. Check REG, C5_CHANNEL and the local device registry.", file=sys.stderr)
    raise SystemExit(1)
PY

sudo install -d -o root -g root -m 0755 /usr/local/libexec/esp32c5-airdrop /etc/esp32c5-airdrop
sudo install -o root -g root -m 0755 "$DESKTOP/c5_device.py" /usr/local/libexec/esp32c5-airdrop/c5_device.py
sudo install -o root -g root -m 0755 "$DESKTOP/esp32c5-airdrop-session" /usr/local/libexec/esp32c5-airdrop/session
sudo install -o root -g root -m 0755 "$DESKTOP/esp32c5-airdrop-control" /usr/local/bin/esp32c5-airdrop-control
sudo install -o root -g root -m 0644 "$temporary/esp32c5-airdrop@.service" /etc/systemd/system/esp32c5-airdrop@.service
sudo install -o root -g root -m 0644 "$temporary/70-esp32c5-airdrop.rules" /etc/udev/rules.d/70-esp32c5-airdrop.rules
sudo install -o root -g root -m 0644 "$DESKTOP/49-esp32c5-airdrop-polkit.rules" /etc/polkit-1/rules.d/49-esp32c5-airdrop.rules
sudo install -o root -g root -m 0644 "$temporary/devices.json" "$temporary/regdomain" "$temporary/channel" /etc/esp32c5-airdrop/
sudo systemctl daemon-reload
sudo udevadm control --reload-rules

mkdir -p "$HOME/.local/bin"
ln -sfn "$ROOT/bin/esp32c5-airdrop" "$HOME/.local/bin/esp32c5-airdrop"
if kpackagetool6 --type Plasma/Applet --show org.esp32c5.airdrop >/dev/null 2>&1; then
  kpackagetool6 --type Plasma/Applet --upgrade "$DESKTOP/plasmoid"
else
  kpackagetool6 --type Plasma/Applet --install "$DESKTOP/plasmoid"
fi
qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript \
  "$(<"$DESKTOP/add-widget.js")" >/dev/null

while IFS= read -r interface; do
  sudo udevadm trigger --action=add --subsystem-match=tty --sysname-match="$interface"
done < "$temporary/interfaces"
sudo udevadm settle --timeout=10
while IFS= read -r interface; do
  sudo systemctl start --no-block "esp32c5-airdrop@$interface.service"
done < "$temporary/interfaces"
echo "C5 desktop integration installed. Plugging in this registered C5 opens a 10-minute receive window."
echo "The panel button starts another window; unplugging hides it. Normal Wi-Fi is unchanged."
