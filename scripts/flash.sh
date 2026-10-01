#!/bin/bash
# Flash firmware to Pico2

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

# Build dir can be passed as $1 (cmake passes ${CMAKE_CURRENT_BINARY_DIR}), defaults to build/
BUILD_DIR="${1:-$PROJECT_DIR/build}"
UF2_FILE="$BUILD_DIR/raiden_pico.uf2"
# Where the RP2350 mass-storage volume shows up depends on the automounter:
# udisks2 uses /run/media/$USER, older setups /media/$USER. And on a desktop
# with no session automounter it is not mounted at all — mount it ourselves
# (udisksctl needs no root; the volume is a removable device owned by us).
MOUNT_CANDIDATES=("/media/${USER}/RP2350" "/run/media/${USER}/RP2350")

find_mount() {
    for m in "${MOUNT_CANDIDATES[@]}"; do
        [ -d "$m" ] && { echo "$m"; return 0; }
    done
    return 1
}

mount_rp2350() {
    local dev
    dev=$(lsblk -rno NAME,LABEL | awk '$2 == "RP2350" { print "/dev/"$1; exit }')
    [ -n "$dev" ] || return 1
    command -v udisksctl >/dev/null || return 1
    udisksctl mount -b "$dev" >/dev/null 2>&1 || return 1
    find_mount >/dev/null
}

# Check if UF2 exists
if [ ! -f "$UF2_FILE" ]; then
    echo "✗ Firmware not found: $UF2_FILE"
    echo "  Run 'make' in the build directory first"
    exit 1
fi

# Reboot to bootloader.
# Opening the CDC port makes the firmware restart, so a command sent 0.5 s
# later lands in the middle of its init and is dropped -- which looked like
# "REBOOT BL is broken". Wait for the CLI to answer, then send it.
echo "Rebooting Pico2 to bootloader mode..."
if [ -c "/dev/ttyACM0" ]; then
    python3 - <<'PY'
import serial, time
with serial.Serial('/dev/ttyACM0', 115200, timeout=0.3) as s:
    deadline = time.time() + 8
    while time.time() < deadline:          # wait for a live prompt
        s.reset_input_buffer()
        s.write(b'VERSION\r\n'); s.flush()
        time.sleep(0.6)
        if b'Raiden' in s.read(4096):
            break
    s.write(b'REBOOT BL\r\n'); s.flush()
    time.sleep(0.3)
PY
else
    echo "✗ /dev/ttyACM0 not available"
    echo "  Device may already be in bootloader mode or not connected"
fi

# Wait for the volume, mounting it ourselves if nothing else does
echo "Waiting for RP2350 bootloader mount..."
MOUNT_POINT=""
for i in {1..15}; do
    MOUNT_POINT=$(find_mount) && break
    mount_rp2350 && MOUNT_POINT=$(find_mount) && break
    sleep 1
done

if [ -z "$MOUNT_POINT" ]; then
    echo "✗ RP2350 not mounted after 15 seconds"
    echo "  Check if device is in bootloader mode (hold BOOTSEL button)"
    exit 1
fi
echo "✓ Device ready at $MOUNT_POINT"

# Flash — the mount can appear before it is writable, so retry the copy and
# check cp's exit status (never report success on a failed copy).
echo "Flashing raiden_pico.uf2..."
CP_ERR=$(mktemp)
flashed=0
for attempt in 1 2 3 4 5; do
    if cp "$UF2_FILE" "$MOUNT_POINT/" 2>"$CP_ERR"; then
        sync 2>/dev/null || true   # device reboots on accept; sync is best-effort
        flashed=1
        break
    fi
    echo "  copy attempt $attempt failed, retrying..."
    sleep 1
done
if [ "$flashed" -ne 1 ]; then
    echo "✗ Flash FAILED — could not copy UF2 to $MOUNT_POINT"
    [ -s "$CP_ERR" ] && echo "  $(cat "$CP_ERR")"
    rm -f "$CP_ERR"
    exit 1
fi
rm -f "$CP_ERR"
echo "✓ Flash complete"
