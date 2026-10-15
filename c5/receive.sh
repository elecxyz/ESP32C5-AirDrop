#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# The C5 and a private network namespace provide the entire radio path.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1:-} != --inside ]]; then
    notify_dir=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/esp32c5-airdrop-notify.XXXXXXXX")
    export CACHYDROP_C5_NOTIFY_SOCKET="$notify_dir/socket"
    python3 "$here/notify_relay.py" --listen &
    notify_pid=$!
    namespace_pid=
    outer_cleanup() {
        trap - EXIT INT TERM
        if [[ -n $namespace_pid ]]; then
            kill -TERM "$namespace_pid" 2>/dev/null || true
            wait "$namespace_pid" 2>/dev/null || true
        fi
        kill -TERM "$notify_pid" 2>/dev/null || true
        wait "$notify_pid" 2>/dev/null || true
        rm -f -- "$notify_dir/socket"
        rmdir -- "$notify_dir"
    }
    trap outer_cleanup EXIT
    trap 'exit 130' INT TERM
    for _ in {1..50}; do [[ -S $CACHYDROP_C5_NOTIFY_SOCKET ]] && break; sleep 0.02; done
    [[ -S $CACHYDROP_C5_NOTIFY_SOCKET ]] || exit 1
    unshare --user --map-root-user --net bash "$here/receive.sh" --inside "$@" &
    namespace_pid=$!
    wait "$namespace_pid"
    exit $?
fi
shift
port=/dev/ttyACM0
channel=6
country=00
seconds=600
while getopts 'p:c:r:s:' option; do
    case "$option" in
        p) port=$OPTARG;;
        c) channel=$OPTARG;;
        r) country=${OPTARG^^};;
        s) seconds=$OPTARG;;
        *) exit 2;;
    esac
done
[[ $seconds =~ ^[0-9]+$ ]] && ((seconds >= 10 && seconds <= 600)) || exit 2
[[ $country == 00 || $country =~ ^[A-Z]{2}$ ]] || exit 2
[[ $channel =~ ^(6|36|44|149)$ ]] || exit 2
project=$(cd "$here/.." && pwd)
python="$project/.venv-opendrop/bin/python"
bridge="$here/host/build/cachydrop-c5"
[[ -x $bridge && -x $python ]] || { echo "Build the C5 host and install OpenDrop first" >&2; exit 1; }
umask 077
state=${XDG_STATE_HOME:-$HOME/.local/state}/esp32c5-airdrop/c5
mkdir -p "$state"
out=$(mktemp -d "$state/session-XXXXXXXX")
receive_dir=${ESP32C5_AIRDROP_RECEIVE_DIR:-${CACHYDROP_C5_RECEIVE_DIR:-$HOME/Downloads}}
mkdir -p "$receive_dir"
ln -sfn "$out" "$state/current"
bridge_pid=
receiver_pid=
cleanup() {
    trap - EXIT INT TERM
    if [[ -n $receiver_pid ]]; then
        kill -TERM "$receiver_pid" 2>/dev/null || true
        for _ in {1..30}; do kill -0 "$receiver_pid" 2>/dev/null || break; sleep 0.1; done
        kill -KILL "$receiver_pid" 2>/dev/null || true
        wait "$receiver_pid" 2>/dev/null || true
    fi
    if [[ -n $bridge_pid ]]; then
        kill -TERM "$bridge_pid" 2>/dev/null || true
        wait "$bridge_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM
ip link set lo up
bridge_started=$(date +%s)
"$bridge" -p "$port" -c "$channel" -r "$country" -s "$seconds" -v > "$out/radio.log" 2>&1 &
bridge_pid=$!
ready=0
for _ in {1..100}; do
    kill -0 "$bridge_pid" 2>/dev/null || { tail -n 10 "$out/radio.log"; exit 1; }
    address=$(ip -6 -o addr show dev awdlc5 scope link 2>/dev/null || true)
    if [[ -n $address && $address != *tentative* && $address != *dadfailed* ]]; then ready=1; break; fi
    sleep 0.1
done
((ready)) || { echo "C5 interface did not acquire an IPv6 address" >&2; exit 1; }
export AIRDROP_RECEIVED="$here/notify_relay.py"
export CACHYDROP_TIDY="$project/tools/airdrop-tidy"
(
    cd "$receive_dir"
    exec "$python" -u "$here/receiver.py"
) > "$out/receive.log" 2>&1 &
receiver_pid=$!
ready=0
for _ in {1..100}; do
    kill -0 "$receiver_pid" 2>/dev/null || { tail -n 20 "$out/receive.log"; exit 1; }
    if grep -q 'Starting HTTPS server' "$out/receive.log"; then ready=1; break; fi
    sleep 0.1
done
((ready)) || { echo "OpenDrop did not become ready" >&2; exit 1; }
if [[ -n ${C5_STATUS_FILE:-} ]]; then
    python3 - "$C5_STATUS_FILE" "${C5_STATUS_INTERFACE:-${port##*/}}" "$((bridge_started + seconds))" "$project/desktop" <<'PY'
import json, os, sys, time
path, interface, deadline, desktop = sys.argv[1:]
sys.path.insert(0, desktop)
from c5_device import DEVICE_LABEL
expires = int(deadline)
payload = dict(state="receiving", interface=interface, backend="esp32c5", device=DEVICE_LABEL,
               expires_at=expires, remaining=max(0, expires-int(time.time())),
               detail="ESP32C5 AirDrop is visible to nearby iPhones")
temporary = path + ".tmp." + str(os.getpid())
with open(temporary, "w", encoding="utf-8") as f:
    json.dump(payload, f)
os.replace(temporary, path)
PY
fi
echo "ESP32C5 AirDrop ready on channel $channel. Select it in the iPhone's AirDrop sheet."
echo "Files: $receive_dir"
echo "Diagnostics: $out"
while kill -0 "$bridge_pid" 2>/dev/null; do
    kill -0 "$receiver_pid" 2>/dev/null || { echo "OpenDrop stopped" >&2; exit 1; }
    sleep 1
done
wait "$bridge_pid"
