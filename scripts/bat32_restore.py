#!/usr/bin/env python3
"""Restaure la code flash d'un BAT32G135 a partir d'une image, par SWD.

Ecrit par `SWD BAT32 WRITE <addr> <hex> CONFIRM` (256 octets par commande,
programmation octet par octet cote firmware). Ce script existe parce que la
restauration qui a suivi le chip erase du 2026-09-01 **n'a pas tout reecrit**
sans que personne le voie : 20 945 octets etaient restes a 0xFF. Il ne dit
donc « restauration complete » qu'apres avoir **relu la puce** et compare.

★ **Le coeur est halte avant toute lecture.** Coeur en marche, une lecture
flash par SWD ramene par moments ce que le coeur est en train de prefetcher au
lieu du contenu demande : mesure le 2026-09-02, `0x00004910` rendait
`70 47 C0 46` avec le coeur actif et `FF FF FF FF` juste apres un `SWD HALT`,
de facon reproductible. Sans halt, le diff initial est donc faux — il inventait
des blocs a reecrire et des blocs « impossibles » qui n'existaient pas.

★ **La relecture de controle se fait apres un reset.** Juste apres une
programmation, la relecture peut etre servie par le buffer du controleur flash
et confirmer une valeur que la cellule ne porte pas. Un `TARGET RESET` +
reconnexion avant de verifier est ce qui donne son sens a la verification.

Trois garde-fous, tous avant la moindre ecriture :
  1. la puce est relue en entier et compare a l'image : seuls les blocs qui
     different sont ecrits ;
  2. la programmation flash ne fait que passer des 1 a 0. Tout octet exigeant
     un bit 0 -> 1 est detecte et le script **refuse** au lieu d'ecrire un
     resultat faux — il faut alors un erase (`SWD BAT32 CHIPERASE CONFIRM`) ;
  3. `--confirm` est obligatoire : sans lui le script se contente du diff.

Exemple :
    python3 scripts/bat32_restore.py \\
        --image assets/bat32_dump_20260831/bat32_code_flash.bin --confirm

Prerequis : pip install pyserial ; firmware raiden-pico >= v0.11 (SWD BAT32).
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

BLOCK = 256          # maximum d'une commande SWD BAT32 WRITE


def send(ser, cmd, settle=1.0, timeout=120.0, await_marker=False):
    """Envoie une commande et rend la reponse.

    `await_marker` attend explicitement `OK:`/`ERROR` au lieu d'un silence :
    programmer 256 octets de flash prend plusieurs secondes pendant lesquelles
    le firmware n'emet rien, et un simple `settle` coupe la lecture apres
    l'echo, donnant une fausse erreur.
    """
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
            if await_marker:
                # L'echo de la commande contient le mot CONFIRM mais jamais
                # `OK:` ni `ERROR:` en debut de ligne — chercher la reponse.
                tail = buf.decode(errors="replace")
                after_echo = tail.split("\n", 1)[1] if "\n" in tail else ""
                if "OK:" in after_echo or "ERROR" in after_echo:
                    break
        elif buf and not await_marker and time.time() - last > settle:
            break
    return buf.decode(errors="replace")


def connect_and_halt(ser, speed, reset=True):
    """Connexion SWD, puis HALT du coeur.

    Le halt n'est pas un confort : coeur en marche, les lectures flash sont
    polluees par le prefetch du coeur (voir l'en-tete du fichier).
    """
    cmds = ["TARGET BAT32", f"SWD SPEED {speed}"]
    if reset:
        cmds.append("TARGET RESET")
    cmds += ["SWD CONNECT", "SWD HALT"]
    for c in cmds:
        out = send(ser, c, settle=0.4, timeout=25)
        if "ERROR" in out:
            print(f"echec sur `{c}` :\n{out.strip()}")
            return False
    return True


def read_region(ser, base, total):
    """Relit la region par SWD READ, par tranches de 1024 mots."""
    got = {}
    for off in range(0, total, 4096):
        nwords = min(4096, total - off) // 4
        addr = base + off
        txt = send(ser, f"SWD READ 0x{addr:X} {nwords}")
        if "ERROR" in txt:
            print(f"  lecture impossible a 0x{addr:08X} :\n{txt.strip()[-300:]}")
            return None
        for line in txt.splitlines():
            m = re.match(r"^0x([0-9A-Fa-f]{8}):((?:\s+[0-9A-Fa-f]{2})+)\s\s", line)
            if m:
                a = int(m.group(1), 16)
                for i, b in enumerate(m.group(2).split()):
                    got[a + i] = int(b, 16)
    if any(base + i not in got for i in range(total)):
        missing = sum(1 for i in range(total) if base + i not in got)
        print(f"  lecture incomplete : {missing} octets manquants")
        return None
    return bytes(got[base + i] for i in range(total))


def blocks_to_write(cur, ref, base):
    """Blocs de 256 octets alignes qui different. Renvoie (blocs, impossibles)."""
    todo, impossible = [], []
    for off in range(0, len(ref), BLOCK):
        c, r = cur[off:off + BLOCK], ref[off:off + BLOCK]
        if c == r:
            continue
        # La programmation ne fait que 1 -> 0 : (courant & cible) doit rendre
        # la cible, sinon il faut un erase prealable.
        if any((c[i] & r[i]) != r[i] for i in range(len(r))):
            impossible.append(base + off)
        todo.append((base + off, r))
    return todo, impossible


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, help="image de reference (.bin)")
    ap.add_argument("--base", type=lambda x: int(x, 0), default=0x00000000)
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--speed", type=int, default=4)
    ap.add_argument("--confirm", action="store_true",
                    help="ecrire reellement ; sans ce drapeau, diff seulement")
    ap.add_argument("--no-reset", action="store_true")
    args = ap.parse_args()

    ref = open(args.image, "rb").read()
    print(f"image {args.image} : {len(ref)} octets, "
          f"md5 {hashlib.md5(ref).hexdigest()}")

    # serial_for_url accepte /dev/ttyACM0 comme socket://host:port
    with serial.serial_for_url(args.port, 115200, timeout=0.2,
                               write_timeout=5) as ser:
        time.sleep(0.6)
        if not connect_and_halt(ser, args.speed, reset=not args.no_reset):
            return 1

        print("lecture de l'etat courant (coeur halte)...")
        cur = read_region(ser, args.base, len(ref))
        if cur is None:
            return 1
        print(f"puce : md5 {hashlib.md5(cur).hexdigest()}")
        if cur == ref:
            print("deja identique a l'image — rien a faire")
            return 0

        todo, impossible = blocks_to_write(cur, ref, args.base)
        ndiff = sum(1 for i in range(len(ref)) if cur[i] != ref[i])
        print(f"{ndiff} octets a corriger, {len(todo)} blocs de {BLOCK}")
        if impossible:
            print(f"REFUS : {len(impossible)} blocs exigent des bits 0->1 "
                  f"(1er a 0x{impossible[0]:08X}) — un erase est necessaire, "
                  f"la programmation seule ne peut pas les restaurer")
            return 1
        if not args.confirm:
            print("diff seulement (ajouter --confirm pour ecrire)")
            for addr, _ in todo[:5]:
                print(f"  ecrirait 0x{addr:08X}")
            if len(todo) > 5:
                print(f"  ... et {len(todo) - 5} blocs de plus")
            return 0

        print("ecriture...")
        t0 = time.time()
        for n, (addr, data) in enumerate(todo, 1):
            out = send(ser, f"SWD BAT32 WRITE 0x{addr:X} {data.hex().upper()} CONFIRM",
                       timeout=90, await_marker=True)
            if "OK:" not in out:
                print(f"  ECHEC a 0x{addr:08X} : {out.strip()[-200:]}")
                return 1
            if n % 10 == 0 or n == len(todo):
                print(f"  {n}/{len(todo)} blocs ({time.time() - t0:.0f}s)", flush=True)

        # Relire APRES un reset, toujours, meme avec --no-reset : une relecture
        # enchainee sur la programmation peut etre servie par le buffer du
        # controleur flash et confirmer une valeur que la cellule ne porte pas.
        # C'est ce qui a fait passer pour verifiee une restauration qui ne
        # l'etait pas.
        print("reset, puis relecture de verification...")
        if not connect_and_halt(ser, args.speed, reset=True):
            return 1
        after = read_region(ser, args.base, len(ref))

    if after is None:
        return 1
    md5 = hashlib.md5(after).hexdigest()
    print(f"puce apres restauration : md5 {md5}")
    if after == ref:
        print("RESTAURATION COMPLETE ET VERIFIEE")
        return 0
    rest = [i for i in range(len(ref)) if after[i] != ref[i]]
    print(f"INCOMPLETE : {len(rest)} octets encore differents, "
          f"1er a 0x{args.base + rest[0]:08X}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
