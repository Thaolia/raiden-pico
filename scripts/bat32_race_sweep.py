#!/usr/bin/env python3
"""
BAT32G135 reset-release race sweep.

Phase 1 of the campaign described in TPLink_Tapo/07_BAT32G135_FAULTYCAT.md
(doc §9 phase 1, §9bis.6, §9ter): the BAT32G135 posts DBGSTOPCR.SWDIS purely
in firmware, at runtime -- not at reset. Between the release of RESETB and
that write, SWD is alive. This is NOT a fault injection: it costs nothing,
is non-destructive, and if it lands the campaign is over before any glitch
hardware gets involved.

Drives the firmware's own `SWD RACE <delay_us>` primitive (raiden-pico
firmware >= v0.8) once per (delay, shot) point -- the reset-assert/release,
the uncompensated SWD connect, AHB-AP bring-up, and vector read all happen
on-device; this script only supplies the delay to test and logs + classifies
each attempt. (The firmware also has its own `SWD RACE SWEEP <start_us>
<end_us> <step_us> [SHOTS <n>]` that runs the whole sweep on-device --
this script instead loops host-side, one SWD RACE per attempt, to get a
CSV row and a live progress line for every single shot.)

Status: NOT TESTED ON HARDWARE -- no BAT32G135 was available in the session
that wrote this. Validate a single `SWD RACE <delay>` by hand in a terminal
before trusting this loop.

Usage:
  # Coarse sweep across the doc's suggested 0-2ms window (delay in us)
  python3 bat32_race_sweep.py --delay-start 0 --delay-end 2000 --delay-step 10 --shots 3

  # Fine sweep once a coarse pass narrows the window
  python3 bat32_race_sweep.py --delay-start 180 --delay-end 220 --delay-step 1 --shots 10

Prerequisites: pip install pyserial ; firmware raiden-pico >= v0.8 (SWD RACE).
Wiring: GP15->RESETB, GP17->P137/SWCLK, GP18->P40/SWDIO, common ground
(doc §9ter.1's cabling diagram, minus the second/third controller -- SWD
RACE runs the whole sequence from one raiden-pico, no FaultyCat/ST-Link
needed for this phase).

Output: CSV log (timestamp, delay_us, shot, category, sp, pc) + live
progress. Stops immediately at the first SUCCESS -- per the doc (§9bis.3),
Level 0 on the BAT32G135 does not survive the next reset, so the script
prints the follow-up dump commands and exits without touching the target
again.
"""
import argparse
import csv
import queue
import re
import threading
import time
from datetime import datetime

import serial

P = argparse.ArgumentParser(description="BAT32G135 SWD reset-release race sweep")
P.add_argument('--port', default='/dev/ttyACM0', help='Raiden Pico serial port')
P.add_argument('--baud', type=int, default=115200)
P.add_argument('--target', default='BAT32',
                help='TARGET <type> to select before sweeping (default BAT32)')
P.add_argument('--delay-start', type=int, default=0,
                help='Sweep start, microseconds (default 0)')
P.add_argument('--delay-end', type=int, default=2000,
                help="Sweep end, microseconds (default 2000 -- doc §8.3's window)")
P.add_argument('--delay-step', type=int, default=10,
                help='Step, microseconds (default 10)')
P.add_argument('--shots', type=int, default=3,
                help='Attempts per delay value (default 3 -- Carpi et al., repeat 3x)')
P.add_argument('--shot-pause', type=float, default=0.0,
                help='Pause in seconds after each shot (default 0). Eases USB CDC '
                     'load on long back-to-back sweeps -- try 0.05-0.1 if you see '
                     'parse_error entries cluster in the log.')
P.add_argument('--phy-khz', type=int, default=2500,
                help='SWD PHY PIO frequency in kHz (default 2500). SWD RACE requires '
                     'the PIO physical layer -- SWD SPEED 0 mis-samples on a '
                     'flying-wire bench (see v0.10 CHANGELOG / doc §0bis) and is no '
                     'longer accepted. Raise this only after confirming the md5 test '
                     'in the plan still passes at the new frequency.')
P.add_argument('--csv', default=None)
args = P.parse_args()

CSV_FILE = args.csv or f"bat32_race_{datetime.now():%Y%m%d_%H%M%S}.csv"

# Parses: "RACE delay=1234us -> perturbed  SP=0x20000100 PC=0x00000101"
RACE_RE = re.compile(
    r'RACE delay=(\d+)us -> (\S+)\s+SP=0x([0-9A-Fa-f]+) PC=0x([0-9A-Fa-f]+)'
)


class Port:
    """Holds the live Serial handle so cmd() can swap it out in place after
    a hard stall, without every call site needing to know the connection
    was replaced."""
    def __init__(self, dev, baud):
        self.dev = dev
        self.baud = baud
        # timeout court : serial.read(n) attend n octets OU le timeout, il ne
        # rend PAS la main des la premiere donnee. A 0.3 s, chaque reponse plus
        # courte que 4096 octets coutait 0,3 s de latence -- soit des minutes
        # sur un sweep de plusieurs milliers de tirs. La fin de reponse est
        # detectee par le marqueur, pas par ce timeout.
        self.s = serial.serial_for_url(dev, baud, timeout=0.05, write_timeout=5)

    def reopen(self):
        try:
            self.s.close()
        except Exception:
            pass
        time.sleep(0.3)
        self.s = serial.serial_for_url(self.dev, self.baud, timeout=0.05,
                                       write_timeout=5)
        time.sleep(0.3)


def _cmd_once(s, c, timeout, q):
    """Runs in a worker thread -- see cmd()'s docstring for why. Broad
    except: if cmd() gave up and reopened the port out from under us, this
    thread's `s` is a closed handle and any further call on it raises --
    this thread is abandoned (daemon) at that point, so just stop quietly
    instead of spamming a traceback for a stall that's already handled."""
    try:
        s.write((c + '\r\n').encode())
        # read(4096), jamais read(in_waiting) : sur une URL socket:// ce
        # dernier vaut 0 ou 1 et non un nombre d'octets, ce qui plafonnait la
        # lecture a ~49 o/s avec troncature silencieuse (voir _send() de
        # bat32_dump.py). read() respecte le timeout du port sur tout
        # transport.
        out, t0 = '', time.time()
        while time.time() - t0 < timeout:
            chunk = s.read(4096)
            if chunk:
                out += chunk.decode('utf-8', 'ignore')
                if 'OK:' in out or 'ERROR:' in out or out.rstrip().endswith('>'):
                    q.put(out)
                    return
        # Timed out with no OK:/ERROR:/prompt seen -- a late reply for THIS
        # command can still be in flight. Give it a moment to land, then
        # drop it: otherwise it gets prepended to the next command's
        # response and desyncs RACE_RE parsing for the following shot too
        # (observed in long sweeps: a parse_error timeout immediately
        # followed by more garbled entries until the stream resynced on
        # its own).
        # Sur socket://, reset_input_buffer() ne purge que le tampon de l'OS :
        # ce qui est encore en vol cote firmware arrive APRES la purge. Marge
        # plus large qu'en serie, ou la latence est de l'ordre de la ms.
        time.sleep(0.3)
        s.reset_input_buffer()
        q.put(out)
    except Exception:
        pass


def cmd(port, c, timeout=5.0):
    """Send a command, return the raw response text. Same read pattern as
    the doc's own raiden-pico example scripts (§9ter.5/§9quater.3): read
    until an OK:/ERROR: line or the '>' prompt shows up, or timeout.

    Runs the actual serial I/O in a worker thread with a hard deadline of
    timeout+3s. Observed on a long unattended sweep: a single read() call
    hung well past its configured port-level timeout (a CDC-ACM driver
    hiccup, not a firmware hang -- VERSION answered instantly on a fresh
    reconnect once the wedged process was killed). A plain per-call
    timeout can't catch that because the hang is *inside* the blocking
    read; the worker thread is abandoned (daemon) and the port is closed
    and reopened instead, so the sweep self-heals rather than hanging
    forever."""
    q = queue.Queue(maxsize=1)
    t = threading.Thread(target=_cmd_once, args=(port.s, c, timeout, q), daemon=True)
    t.start()
    try:
        return q.get(timeout=timeout + 3)
    except queue.Empty:
        print(f'\n  (hard stall on {c!r} -- reopening {port.dev})')
        port.reopen()
        return ''


def setup(port):
    print(cmd(port, 'VERSION').strip())
    print(cmd(port, f'TARGET {args.target}').strip())
    # Pulse nRST before anything else -- see the long comment in
    # scripts/bat32_dump.py: a target left powered can stop answering SWD
    # entirely (ACK=0x7 at every speed, both physical layers), which is
    # indistinguishable from a locked chip until a reset clears it. Use
    # TARGET RESET, not SWD RESET: the latter auto-connects first and so
    # fails before it can reset anything.
    print(cmd(port, 'TARGET RESET').strip())
    # SWD RACE requires the PIO physical layer (v0.10+) -- SWD SPEED 0
    # (this script's old prerequisite) mis-samples on a flying-wire bench:
    # ACK=0x7 and a DPIDR shifted left by one bit, indistinguishable on the
    # surface from a genuinely locked target. See the v0.10 CHANGELOG entry
    # and §0bis of 07_BAT32G135_FAULTYCAT.md.
    print(cmd(port, f'SWD PHY PIO {args.phy_khz}').strip())


def main():
    n_points = (args.delay_end - args.delay_start) // args.delay_step + 1
    total = n_points * args.shots
    print(f'{total} attempts ({n_points} delay values x {args.shots} shots) -> {CSV_FILE}\n')

    port = Port(args.port, args.baud)
    port.s.reset_input_buffer()
    time.sleep(0.3)
    setup(port)

    n = 0
    with open(CSV_FILE, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['ts', 'delay_us', 'shot', 'category', 'sp', 'pc'])
        try:
            for delay_us in range(args.delay_start, args.delay_end + 1, args.delay_step):
                for shot in range(args.shots):
                    n += 1
                    resp = cmd(port, f'SWD RACE {delay_us}', timeout=3)
                    m = RACE_RE.search(resp)
                    if not m:
                        cat, sp, pc = 'parse_error', 0, 0
                    else:
                        cat = m.group(2)
                        sp = int(m.group(3), 16)
                        pc = int(m.group(4), 16)
                    w.writerow([datetime.now().isoformat(timespec='seconds'),
                                delay_us, shot, cat, f'{sp:08X}', f'{pc:08X}'])
                    fh.flush()
                    print(f'\r[{n}/{total}] delay={delay_us:>6}us shot={shot + 1}/{args.shots} '
                          f'-> {cat:<12}', end='', flush=True)
                    if cat == 'SUCCESS':
                        print(f'\n\n*** SUCCESS at delay={delay_us}us  SP={sp:08X} PC={pc:08X} ***')
                        print('*** TARGET LEFT POWERED + CONNECTED -- DO NOT RESET / POWER-CYCLE ***')
                        print('    Dump now, e.g.:')
                        print('      SWD READ FLASH             # 64KB code flash')
                        print('      SWD READ 0x00500000 384    # 1.5KB data flash')
                        print('      SWD READ 0x0050084C 4      # UID (possible decryption key)')
                        print('      SWD OPT                    # confirm the level reached')
                        return
                    if cat == 'parse_error':
                        print(f'\n  (unparsed response, raw: {resp!r})')
                    if args.shot_pause:
                        time.sleep(args.shot_pause)
        except KeyboardInterrupt:
            print('\ninterrupted')
    print(f'\ndone -- no success. log: {CSV_FILE}')


if __name__ == '__main__':
    main()
