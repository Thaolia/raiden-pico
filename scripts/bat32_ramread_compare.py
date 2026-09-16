#!/usr/bin/env python3
"""Verifie `SWD BAT32 RAMREAD` — la lecture de la flash BAT32G135 PAR LE COEUR.

RAMREAD existe pour un seul cas : la protection Level 1, ou le debugger n'a
plus le droit de lire la flash alors que le coeur, lui, l'a toujours puisqu'il
s'execute depuis elle. Mais un resultat de RAMREAD ne vaut que si le mecanisme
a d'abord ete prouve la ou une reference existe.

Deux modes, dans cet ordre :

  --mode compare  (defaut, Level 0)
      lit chaque tranche par les DEUX chemins — RAMREAD (coeur) et SWD READ
      (debugger) — et exige l'egalite. C'est le test qui valide le mecanisme
      lui-meme : payload injecte en SRAM, PC/SP ecrits, coeur qui s'execute
      depuis la SRAM. Mesure le 2026-09-01 : 64 KB de code flash + 1,5 KB de
      data flash identiques (35 s pour la passe RAMREAD seule a SWD SPEED 4).

  --mode ref --ref <image.bin>  (Level 1)
      au Level 1 `SWD READ` faute : il n'y a plus de second chemin. On compare
      donc RAMREAD a une image de reference prise au Level 0. C'est le test du
      bypass proprement dit.

⚠ RAMREAD ecrase la SRAM cible 0x20000000-0x2000101F, ou reside le `.data`
recopie depuis la flash au boot. Au Level 1, dumper la SRAM AVANT la premiere
passe (`SWD READ SRAM`) : c'est une fuite de contenu flash qui ne coute aucune
ecriture, et RAMREAD la detruit.

Exemples :
    python3 scripts/bat32_ramread_compare.py
    python3 scripts/bat32_ramread_compare.py --base 0x500000 --len 1536
    python3 scripts/bat32_ramread_compare.py --mode ref \
        --ref assets/bat32_dump_20260831/bat32_code_flash.bin --out /tmp/l1.bin

Prerequis : pip install pyserial ; firmware raiden-pico >= v0.13.
"""
import argparse
import hashlib
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial manquant : pip install pyserial")

CHUNK_WORDS = 1024          # 4 KB — le maximum d'une passe RAMREAD


def send(ser, cmd, settle=1.0, timeout=120.0):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    ser.flush()
    t0 = last = time.time()
    buf = b""
    while time.time() - t0 < timeout:
        chunk = ser.read(8192)
        if chunk:
            buf += chunk
            last = time.time()
        elif buf and time.time() - last > settle:
            break
    return buf.decode(errors="replace")


def parse_words(txt, base, nwords):
    """Sortie RAMREAD : `0xADDR: WWWWWWWW WWWWWWWW ...` (4 mots par ligne)."""
    got = {}
    for line in txt.splitlines():
        m = re.match(r"^0x([0-9A-Fa-f]{8}):((?:\s+[0-9A-Fa-f]{8})+)\s*$", line.strip())
        if m:
            addr = int(m.group(1), 16)
            for i, w in enumerate(m.group(2).split()):
                got[addr + 4 * i] = int(w, 16)
    # Place par ADRESSE, jamais par concatenation : une ligne perdue coute ses
    # propres octets au lieu de decaler tout ce qui suit.
    if any(base + 4 * i not in got for i in range(nwords)):
        return None
    return b"".join(got[base + 4 * i].to_bytes(4, "little") for i in range(nwords))


def parse_bytes(txt, base, nbytes):
    """Sortie SWD READ : hexdump 16 octets par ligne."""
    got = {}
    for line in txt.splitlines():
        m = re.match(r"^0x([0-9A-Fa-f]{8}):((?:\s+[0-9A-Fa-f]{2})+)\s\s", line)
        if m:
            addr = int(m.group(1), 16)
            for i, b in enumerate(m.group(2).split()):
                got[addr + i] = int(b, 16)
    if any(base + i not in got for i in range(nbytes)):
        return None
    return bytes(got[base + i] for i in range(nbytes))


def connect(ser, speed, reset=True):
    cmds = ["TARGET BAT32", f"SWD SPEED {speed}"]
    if reset:
        # Sans ce reset la cible imite une puce verrouillee a toutes les
        # vitesses (voir 07_BAT32G135_FAULTYCAT.md §0bis).
        cmds.append("TARGET RESET")
    # HALT avant toute lecture : coeur en marche, une lecture flash par SWD
    # ramene par moments ce que le coeur prefetche au lieu de l'adresse
    # demandee (mesure le 2026-09-02 : `0x00004910` rendait `70 47 C0 46`
    # coeur actif, `FF FF FF FF` juste apres un halt). RAMREAD halte de
    # lui-meme, mais le `SWD READ` auquel on le compare, non — sans ce halt
    # la comparaison accuserait RAMREAD d'une divergence qui vient de l'autre
    # chemin.
    cmds += ["SWD CONNECT", "SWD HALT"]
    for c in cmds:
        out = send(ser, c, settle=0.4, timeout=25)
        if "ERROR" in out:
            print(f"echec sur `{c}` :\n{out.strip()}")
            return False
    return True


def read_region(ser, base, total, want_debugger):
    """Renvoie (via_coeur, via_debugger|None) ou (None, None) en cas d'echec."""
    core, dbg = bytearray(), bytearray()
    for off in range(0, total, CHUNK_WORDS * 4):
        nwords = min(CHUNK_WORDS * 4, total - off) // 4
        addr = base + off
        out = send(ser, f"SWD BAT32 RAMREAD 0x{addr:X} {nwords}")
        blob = None if "ERROR" in out else parse_words(out, addr, nwords)
        if blob is None:
            print(f"  RAMREAD a echoue a 0x{addr:08X} :\n{out.strip()[-500:]}")
            return None, None
        core += blob
        if want_debugger:
            out = send(ser, f"SWD READ 0x{addr:X} {nwords}")
            blob = None if "ERROR" in out else parse_bytes(out, addr, nwords * 4)
            if blob is None:
                print(f"  SWD READ a echoue a 0x{addr:08X} — protection active ?"
                      f" Utiliser --mode ref.\n{out.strip()[-300:]}")
                return None, None
            dbg += blob
        print(f"  0x{addr:08X} +{nwords * 4} ok", flush=True)
    return bytes(core), (bytes(dbg) if want_debugger else None)


def report_diff(a, b, base, label_a, label_b):
    diffs = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    print(f"DIFFERENT : {len(diffs)} octets sur {len(a)}")
    zones, start, prev = [], diffs[0], diffs[0]
    for i in diffs[1:]:
        if i - prev > 16:
            zones.append((start, prev))
            start = i
        prev = i
    zones.append((start, prev))
    for lo, hi in zones[:10]:
        print(f"  0x{base + lo:08X}-0x{base + hi:08X}  "
              f"{label_a}={a[lo:lo + 4].hex()} {label_b}={b[lo:lo + 4].hex()}")
    if len(zones) > 10:
        print(f"  ... {len(zones) - 10} zones de plus")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--speed", type=int, default=4,
                    help="SWD SPEED (defaut 4 ~125 kHz ; 0 ne marche pas sur ce banc)")
    ap.add_argument("--base", type=lambda x: int(x, 0), default=0x00000000)
    ap.add_argument("--len", dest="length", type=lambda x: int(x, 0), default=0x10000)
    ap.add_argument("--mode", choices=("compare", "ref"), default="compare")
    ap.add_argument("--ref", help="image de reference (obligatoire en --mode ref)")
    ap.add_argument("--ref-offset", type=lambda x: int(x, 0), default=0,
                    help="offset de --base DANS le fichier de reference "
                         "(defaut 0 : la reference commence a --base)")
    ap.add_argument("--out", help="ecrire le resultat RAMREAD dans ce fichier")
    ap.add_argument("--no-reset", action="store_true",
                    help="ne pas pulser TARGET RESET avant SWD CONNECT")
    args = ap.parse_args()

    if args.mode == "ref" and not args.ref:
        ap.error("--mode ref exige --ref <image.bin>")
    reference = None
    if args.ref:
        blob = open(args.ref, "rb").read()
        reference = blob[args.ref_offset:args.ref_offset + args.length]
        if len(reference) < args.length:
            ap.error(f"{args.ref} ne fournit que {len(reference)} octets a partir "
                     f"de l'offset 0x{args.ref_offset:X} (il en faut {args.length})")

    # serial_for_url accepte /dev/ttyACM0 comme socket://host:port
    with serial.serial_for_url(args.port, 115200, timeout=0.2,
                               write_timeout=5) as ser:
        time.sleep(0.6)
        if not connect(ser, args.speed, reset=not args.no_reset):
            return 1
        t0 = time.time()
        core, dbg = read_region(ser, args.base, args.length,
                                want_debugger=(args.mode == "compare"))
    if core is None:
        return 1

    md5 = hashlib.md5(core).hexdigest()
    print(f"\nRAMREAD : {len(core)} octets @ 0x{args.base:08X} en "
          f"{time.time() - t0:.0f}s, md5 {md5}")
    if args.out:
        open(args.out, "wb").write(core)
        print(f"ecrit dans {args.out}")

    other, label = (dbg, "SWD READ") if args.mode == "compare" else (reference, args.ref)
    if core == other:
        print(f"IDENTIQUE a {label}")
        return 0
    report_diff(core, other, args.base, "coeur", "ref")
    return 1


if __name__ == "__main__":
    sys.exit(main())
