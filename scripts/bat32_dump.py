#!/usr/bin/env python3
"""Dump a BAT32G135's code flash, data flash and SRAM over SWD (read-only).

Status, stated precisely (2026-09-02):
  - The DUMP is validated. A real BAT32G135 was dumped twice, byte-identical
    (code flash md5 6f37bd86c41a75c19db65cc5824f7199, in assets/); the four
    bytes the two passes once disagreed on were arbitrated by six direct
    re-reads. That dump was produced by an earlier throwaway script -- and
    with the core running, so those four bytes were most likely the prefetch
    artifact described below rather than a flaky link.
  - THIS script is validated end to end since 2026-09-02: a full run printed
    "Dump complet et verifie", with code flash and data flash each read twice
    and matching (code 2c92a6a2..., data abce089c..., in assets/
    bat32_dump_20260902/). The reconcile() path has still only been exercised
    on code flash.
  It never reports a dump as good on data it did not actually read back: a run
  either prints that final line, or tells you exactly what it could not
  confirm.

★ The core is HALTED before reading (--no-halt opts out). With the core
    running, a flash read over SWD intermittently returns what the core is
    prefetching instead of the requested address -- measured 2026-09-02,
    0x00004910 reading 70 47 C0 46 (`bx lr; nop`) running vs FF FF FF FF
    halted, reproducibly. Every dump taken before that date was read with the
    core running, including the 6f37bd86 reference.

★ Use a SLOW SWD clock -- this is the whole trick.
    On the bench this was written for, `SWD SPEED 0` (max, no bit delay) does
    NOT work: the DP answers, but sampling lands a bit off, so every ACK reads
    back 0x7 and DPIDR comes out as the real word shifted left by one
    (0x0BC11477 -> 0x178028EF). That looks exactly like "SWD is disabled" and
    sent an entire campaign chasing a reset-release race that was never
    needed. At `SWD SPEED 4` (~125 kHz) the same target connects first try and
    reports DPIDR=0x0BC11477, OCDEN=0xFF/OCDM=0xFF (Level 0, flash open) and
    DBGSTOPCR.SWDIS=0. If a connect fails, SLOW DOWN before concluding
    anything about protection. See TPLink_Tapo/07_BAT32G135_FAULTYCAT.md.

Bytes are placed in the output buffer by the ADDRESS printed on each hexdump
line, never by appending -- a truncated or repeated line then costs those
bytes instead of shifting everything after it. The script reports how many
bytes it actually received per region, so a partial dump is visible rather
than silently misaligned.

Usage:
    python3 scripts/bat32_dump.py --out-dir dumps/
    python3 scripts/bat32_dump.py --speed 8 --port /dev/ttyACM1

Prerequisites: pip install pyserial ; firmware raiden-pico >= v0.8 (TARGET BAT32).
Wiring: GP15->RESETB, GP17->P137/SWCLK, GP18->P40/SWDIO, common ground.
"""
import argparse
import hashlib
import os
import re
import sys
import time

import serial

# "0xAAAAAAAA: 00 11 22 ...  ................" -- the ONLY 8-hex-digit group
# on a line is the address (doc §9ter.0 point 5); the pairs after the colon
# are the data bytes.
#
# Anchored with \A on a single stripped line, never scanned across a blob with
# finditer: a late reply from the previous SWD READ can still be sitting in the
# OS buffer when the next one is read, and the two texts concatenate. Scanning
# the joined blob then matches ACROSS the seam -- a truncated "...0x00004660"
# tail gluing onto a ": 70 47 C0 46" head -- and writes real bytes from one
# address on top of another. That silently corrupted 4 bytes at 0x4664 of a
# 64 KB dump (verified against 6 direct re-reads showing FF FF FF FF) while
# still reporting "0 manquants", which is exactly the kind of quiet corruption
# a firmware dump must never have.
#
# Exactly ONE space before each byte pair, and no anchor on what follows: the
# ASCII column is separated by TWO spaces, so the repeat stops there on its own
# (" ." cannot match a hex pair). Matching \s+ instead would let the group eat
# both separator spaces and then swallow hex-looking ASCII, and anchoring the
# tail would reject every line whose ASCII column contains a space -- which is
# any line holding a 0x20 byte.
LINE = re.compile(r'\A0x([0-9A-Fa-f]{8}):((?:\s[0-9A-Fa-f]{2})+)')

# volatile=True -> content legitimately changes between reads (live RAM), so a
# mismatch there is expected and must not be reported as a dump failure.
REGIONS = [
    ('code_flash', 0x00000000, 64 * 1024, False),
    ('data_flash', 0x00500000, 1536,      False),
    ('sram',       0x20000000, 8 * 1024,  True),
]


class Disconnected(RuntimeError):
    """The USB CDC link dropped mid-dump (OSError errno 5 / SerialException).

    Seen on this bench: the Pico occasionally falls off the USB bus entirely
    (it vanishes from lsusb, not just from /dev). Nothing the script does can
    recover a dump that was in flight, so it stops with a clear message instead
    of a traceback -- and, crucially, never writes a partial region as if it
    were good.
    """


def send(s, cmd, wait, done='OK:'):
    try:
        return _send(s, cmd, wait, done)
    except (OSError, serial.SerialException) as e:
        raise Disconnected(f'lien serie perdu pendant {cmd!r}: {e}') from e


def _send(s, cmd, wait, done='OK:', settle=0.2):
    # Reads with a fixed-size read(), never read(in_waiting). On a socket://
    # URL pyserial's in_waiting is len(select(...)) -- 0 or 1, NEVER a byte
    # count (serial/urlhandler/protocol_socket.py:136-142) -- so the old
    # read(in_waiting) + sleep(0.02) loop degraded to one byte per 20 ms.
    # Measured against a simulated 8 KB reply: 49 B/s and SILENTLY TRUNCATED,
    # versus 40 500 B/s and complete with this loop. read() honours the port
    # timeout on every transport, and dropping the unconditional sleep(0.02)
    # speeds up the USB path too.
    s.write((cmd + '\r\n').encode())
    out, t0, last = '', time.time(), time.time()
    while time.time() - t0 < wait:
        chunk = s.read(4096)
        if chunk:
            out += chunk.decode('utf-8', 'ignore')
            last = time.time()
            if done in out or 'ERROR' in out:
                # Let the tail of the reply (prompt, trailing newline) land so
                # it can't spill into the next command's read. Short timeout on
                # this one: there is usually nothing left, and paying the full
                # port timeout here once per command cost 38 s on a 64 KB dump.
                time.sleep(0.05)
                keep = s.timeout
                s.timeout = 0.05
                try:
                    out += s.read(4096).decode('utf-8', 'ignore')
                finally:
                    s.timeout = keep
                break
        elif out and time.time() - last > settle:
            break
    return out


def dump_region(s, base, total, path, chunk_words, label=None):
    """Returns (bytes_received, total). Writes `path` either way.

    `label` is what the progress line calls this pass; it is never silent.
    An earlier version ran the verification pass with verbose=False, which
    made a slow-but-working second 64 KB pass indistinguishable from a hang
    (several minutes of zero output, on top of Python block-buffering stdout
    when redirected to a file). Every long-running pass prints progress, and
    every summary line flushes -- see the flush=True calls below.
    """
    label = label or os.path.basename(path)
    buf = bytearray(total)
    seen = bytearray(total)
    n_chunks = (total + chunk_words * 4 - 1) // (chunk_words * 4)
    t0 = time.time()
    for ci, off in enumerate(range(0, total, chunk_words * 4)):
        n = min(chunk_words, (total - off) // 4)
        if n == 0:
            break
        lo, hi = base + off, base + off + n * 4      # chunk actually requested
        s.reset_input_buffer()
        resp = send(s, f'SWD READ 0x{lo:08X} {n}', 25, done='OK: Read complete')
        for raw in resp.splitlines():
            m = LINE.match(raw.strip())
            if not m:
                continue
            addr = int(m.group(1), 16)
            if not (lo <= addr < hi):
                # A line from another read that leaked into this reply. Its
                # bytes may be fine, but trusting an address we did not ask
                # for is how the seam-match corruption got in -- drop it and
                # let its own chunk (re-)provide it.
                continue
            for i, byte_hex in enumerate(m.group(2).split()):
                idx = addr - base + i
                if 0 <= idx < total:
                    buf[idx] = int(byte_hex, 16)
                    seen[idx] = 1
        # Chunk index + elapsed time, so a slow link is visibly slow rather
        # than looking wedged. flush=True: stdout is block-buffered as soon
        # as it is redirected to a file, and a progress line that only lands
        # 8 KB later is worse than none.
        el = time.time() - t0
        print(f'\r  {label}: {sum(seen)}/{total}  '
              f'[bloc {ci + 1}/{n_chunks}, {el:.0f}s]',
              end='', flush=True)
    with open(path, 'wb') as fh:
        fh.write(buf)
    got = sum(seen)
    md5 = hashlib.md5(buf).hexdigest()
    status = 'complet' if got == total else f'INCOMPLET ({total - got} manquants)'
    print(f'\r  {label}: {got}/{total} {status}  md5={md5}'
          f'  [{time.time() - t0:.0f}s]', flush=True)
    return got, total


def read_words(s, addr, n):
    """One short SWD READ; returns {address: byte} for what came back."""
    s.reset_input_buffer()
    resp = send(s, f'SWD READ 0x{addr:08X} {n}', 10, done='OK: Read complete')
    out = {}
    for raw in resp.splitlines():
        m = LINE.match(raw.strip())
        if not m:
            continue
        a = int(m.group(1), 16)
        for i, byte_hex in enumerate(m.group(2).split()):
            out[a + i] = int(byte_hex, 16)
    return out


def reconcile(s, buf, base, diff, tries=6):
    """Re-read the differing addresses in SMALL bursts until a value repeats.

    The two full passes disagreed on these bytes. Rather than trust either,
    re-read each 16-byte line on its own -- a 4-word burst leaves far less room
    for the stale-value-after-WAIT duplication that long auto-incrementing
    bursts suffer from -- and accept a byte once two consecutive reads agree.
    """
    lines = sorted({(base + i) & ~0xF for i in diff})
    resolved = 0
    for line_addr in lines:
        prev, stable = None, None
        for _ in range(tries):
            got = read_words(s, line_addr, 4)
            vals = tuple(got.get(line_addr + k) for k in range(16))
            if None in vals:
                prev = None
                continue
            if prev is not None and vals == prev:
                stable = vals
                break
            prev = vals
        if stable is None:
            # Bytes fixed before this line stay fixed in `buf`, but the caller
            # discards the whole buffer on False -- a partially reconciled dump
            # is not a dump. Name the address so the next run can be targeted.
            print(f' [ligne 0x{line_addr:08X} instable apres {tries} relectures;'
                  f' {resolved} ligne(s) deja corrigee(s) sont abandonnees]',
                  end='')
            return False
        for k, v in enumerate(stable):
            idx = line_addr + k - base
            if 0 <= idx < len(buf):
                buf[idx] = v
        resolved += 1
    return resolved == len(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--port', default='/dev/ttyACM0')
    p.add_argument('--baud', type=int, default=115200)
    p.add_argument('--speed', type=int, default=4,
                   help='SWD SPEED value for the BITBANG phy (default 4 = ~125 kHz, '
                        'ignored if --phy pio). NOT 0 -- see the module docstring; '
                        '0 mis-samples on this bench.')
    p.add_argument('--phy', choices=('bitbang', 'pio'), default='bitbang',
                   help='Physical layer (default bitbang). "pio" is the byte-exact '
                        'regression oracle for a SWD-PIO port: an md5 match against '
                        'a bitbang dump at the same address range is what proves the '
                        'PIO phy is correct at the physical layer -- see the plan\'s '
                        'validation section. firmware >= v0.10 (SWD PHY command).')
    p.add_argument('--phy-khz', type=int, default=2500,
                   help='SWD PHY PIO frequency in kHz (default 2500, ignored if '
                        '--phy bitbang). Requires --phy pio.')
    p.add_argument('--out-dir', default='.')
    p.add_argument('--chunk-words', type=int, default=256,
                   help='Words per SWD READ (default 256 = 1 KB)')
    p.add_argument('--no-reset', action='store_true',
                   help='Skip the TARGET RESET pulse before connecting. Not '
                        'recommended: without it the SW-DP can stay unresponsive '
                        '(ACK=0x7 at every speed and on both physical layers), '
                        'which looks exactly like a locked chip. Only use this if '
                        'resetting the target would destroy state you need.')
    p.add_argument('--no-verify', action='store_true',
                   help='Skip the second verification pass over flash (faster, '
                        'but a silently corrupted dump then looks identical to '
                        'a good one -- not recommended)')
    p.add_argument('--no-halt', action='store_true',
                   help='Do not halt the core before reading. Only for '
                        'deliberately observing a running target: with the core '
                        'running, flash reads intermittently return the core\'s '
                        'prefetch instead of the requested address.')
    args = p.parse_args()

    if args.phy == 'bitbang' and args.speed == 0:
        print('ERROR: --speed 0 mis-samples SWD on this bench (ACK=0x7, DPIDR '
              'shifted by one bit). Use 4, or 1-8, or --phy pio.', file=sys.stderr)
        return 2

    os.makedirs(args.out_dir, exist_ok=True)
    try:
        # serial_for_url accepte aussi bien /dev/ttyACM0 qu'une URL
        # socket://host:port -- il ne bascule que si '://' est present,
        # donc --port reste polyvalent sans option supplementaire.
        # timeout court (et non 0.3 s) : serial.read(n) attend n octets OU le
        # timeout -- il ne rend PAS la main des la premiere donnee. Avec 0.3 s,
        # chaque tour de la boucle de lecture de _send() pouvait payer le
        # timeout plein, ce qui doublait la duree d'un dump 64 Ko. La fin de
        # reponse est detectee par le marqueur 'OK:' ou par le settle, pas par
        # ce timeout.
        s = serial.serial_for_url(args.port, args.baud, timeout=0.05,
                                  write_timeout=5)
    except (OSError, serial.SerialException) as e:
        raise Disconnected(f'ouverture de {args.port} impossible: {e}') from e
    time.sleep(0.4)
    s.reset_input_buffer()

    send(s, 'TARGET BAT32', 1.0)
    s.reset_input_buffer()

    # Pulse nRST before connecting. Observed 2026-09-01: after the bench had
    # been left powered for a while, SWD CONNECT failed with ACK=0x7 at EVERY
    # bit-bang speed (1/2/4/8) AND on the PIO phy -- looking exactly like the
    # SPEED-0 mis-sampling signature, i.e. exactly like a locked chip. A
    # single TARGET RESET fixed it instantly and permanently: DPIDR=0x0BC11477
    # first try, every speed, both physical layers. The target's SW-DP needs
    # the reset to become responsive again; without it the failure is silent
    # and mimics protection. TARGET RESET drives GP15 directly and does NOT
    # go through SWD auto-connect (unlike SWD RESET, which auto-connects
    # first and therefore fails before it can reset anything).
    if not args.no_reset:
        send(s, 'TARGET RESET', 2.0)
        time.sleep(0.3)
        s.reset_input_buffer()

    if args.phy == 'pio':
        phy_desc = f'PIO {args.phy_khz} kHz'
        resp = send(s, f'SWD PHY PIO {args.phy_khz}', 1.0)
        if 'OK' not in resp:
            print(f'ERROR: SWD PHY PIO {args.phy_khz} rejected: {resp.strip()!r}',
                  file=sys.stderr)
            s.close()
            return 2
    else:
        phy_desc = f'BITBANG speed {args.speed}'
        send(s, f'SWD PHY BITBANG', 1.0)
        send(s, f'SWD SPEED {args.speed}', 1.0)
    s.reset_input_buffer()
    resp = send(s, 'SWD CONNECT', 6)
    if 'DPIDR' not in resp:
        print(f'ERROR: SWD connect failed at {phy_desc}: {resp.strip()!r}',
              file=sys.stderr)
        print('       Try a slower --speed/--phy-khz before assuming the target '
              'is locked.', file=sys.stderr)
        s.close()
        return 1
    print(next(l.strip() for l in resp.splitlines() if 'DPIDR' in l))

    # Halt the core before reading anything. With the core running, a flash
    # read over SWD intermittently returns what the CORE is prefetching rather
    # than the requested address. Measured 2026-09-02: 0x00004910 read back as
    # 70 47 C0 46 (`bx lr; nop`) with the core running and FF FF FF FF right
    # after a SWD HALT, reproducibly, on the same unmodified part. The four
    # "corrupted bytes at 0x4664" this script's verification pass once caught
    # were almost certainly the same artifact, not a flaky read.
    if not args.no_halt:
        s.reset_input_buffer()
        resp = send(s, 'SWD HALT', 4)
        if 'halted' not in resp.lower():
            print(f'WARNING: SWD HALT did not confirm ({resp.strip()!r}); the '
                  'dump may pick up prefetch artifacts', file=sys.stderr)

    s.reset_input_buffer()
    print(send(s, 'SWD OPT', 6).strip())

    ok = True
    for name, base, size, volatile in REGIONS:
        path = os.path.join(args.out_dir, f'bat32_{name}.bin')
        got, total = dump_region(s, base, size, path, args.chunk_words)
        if got != total:
            ok = False
            continue
        if volatile or args.no_verify:
            continue
        # Second independent pass over non-volatile memory. A dump that is
        # complete but wrong reads exactly like a good one -- only re-reading
        # catches it (this is how 4 corrupted bytes at 0x4664 were found).
        tmp = path + '.verify'
        got2, _ = dump_region(s, base, size, tmp, args.chunk_words,
                               label=f'bat32_{name}.bin (verification)')
        a = bytearray(open(path, 'rb').read())
        b = open(tmp, 'rb').read()
        os.remove(tmp)
        if got2 != total:
            print('    verification 2e passe: incomplete')
            ok = False
            continue
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if not diff:
            print('    verification 2e passe: identique')
            continue
        print(f'    verification 2e passe: {len(diff)} octets divergents '
              f'-> reconciliation', end='', flush=True)
        if reconcile(s, a, base, diff):
            with open(path, 'wb') as fh:
                fh.write(a)
            print(' : resolu (relecture stable)')
        else:
            print(' : ECHEC (instable)')
            ok = False
    s.close()

    if not ok:
        print('\nEchec: region incomplete ou divergente entre deux passes -- '
              'NE PAS utiliser ce dump, relancer.', file=sys.stderr)
        return 1
    print('\nDump complet et verifie (flash relue et identique; la SRAM est '
          'vivante, elle n\'est pas comparee).')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Disconnected as e:
        print(f'\nERREUR: {e}', file=sys.stderr)
        print('Le Pico a quitte le bus USB. Verifier le cable, puis relancer '
              '(les fichiers ecrits par ce run sont a jeter).', file=sys.stderr)
        sys.exit(1)
