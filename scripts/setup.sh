#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

packages=(base-devel cmake ninja dolphin git libev libnl libnotify libpcap zlib python python-pip)
missing=()
for package in "${packages[@]}"; do
  pacman -Q "$package" >/dev/null 2>&1 || missing+=("$package")
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "Installing CachyOS dependencies: ${missing[*]}"
  sudo pacman -S --needed "${missing[@]}"
fi

git submodule update --init --recursive
cmake -S c5/host -B c5/host/build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build c5/host/build -j"$(nproc)"

python -m venv --clear .venv-opendrop
.venv-opendrop/bin/pip install 'opendrop==0.13.0' 'setuptools<81' pyserial
site=$(.venv-opendrop/bin/python -c 'import site; print(site.getsitepackages()[0])')
for name in \
  ios26-airdrop recv-window py314-send ask-confirm mdns-reannounce chunk-reader \
  received-hook stable-receiver
do
  git apply --check --unsafe-paths --directory="$site" "patches/opendrop-$name.patch"
  git apply --unsafe-paths --directory="$site" "patches/opendrop-$name.patch"
done

.venv-opendrop/bin/python -m compileall -q "$site/opendrop"
.venv-opendrop/bin/opendrop --help >/dev/null

mkdir -p "$HOME/.local/bin" "${XDG_CONFIG_HOME:-$HOME/.config}/esp32c5-airdrop"
ln -sfn "$ROOT/bin/esp32c5-airdrop" "$HOME/.local/bin/esp32c5-airdrop"

config="${XDG_CONFIG_HOME:-$HOME/.config}/esp32c5-airdrop/config"
if [ ! -e "$config" ]; then
  printf '%s\n' \
    'REG="00"' \
    'C5_CHANNEL="6"' \
    > "$config"
fi

echo
echo "ESP32C5 AirDrop is built. Flash the C5 firmware, then run ./scripts/install-desktop.sh."
