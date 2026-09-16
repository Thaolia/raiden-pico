#!/usr/bin/env python3
"""Sauvegarde et restaure la DATA FLASH d'un BAT32G135 (0x00500000, 1,5 Ko).

Statut, dit precisement (2026-09-02) :
  - `backup` est **exerce** : double lecture concordante sur 1536 octets et sur
    1024 (`--len 0x400`), coeur halte, OCDM lu a 0xFF.
  - `restore` est **exerce de bout en bout** : l'image du 2026-09-01 reposee sur
    une data flash fraichement blanchie, bloc temoin verifie, puis relecture
    apres reset a `4a5114b8114f61b1c8eb03537406a3cf` — exact. Le refus
    « bits 0->1 » et le court-circuit « deja identique » ont ete exerces aussi.
  ⚠ Blanchir la data flash demande `SWD BAT32 SECTORERASE` sur `0x500000`,
    `0x500200` et `0x500400` : **le chip erase ne la touche pas** (secteur de
    data flash = 512 octets).

Pourquoi ce script existe : sur le banc, **l'appairage du capteur vit dans la
data flash**. Or la seule sortie du Level 1 est un `SWD BAT32 CHIPERASE`, qui
efface *tout* — code flash **et** data flash. Sans sauvegarde prise avant, le
test du Level 1 se paie par un ré-appairage.

    # AVANT toute manip qui peut effacer (Level 1, chip erase, glitch)
    python3 scripts/bat32_dataflash.py backup

    # APRES un chip erase, pour retrouver l'appairage
    python3 scripts/bat32_dataflash.py restore --image <fichier> --confirm

★ Le cœur est halté avant toute lecture : cœur en marche, une lecture flash par
SWD ramène par moments le prefetch du cœur au lieu de l'adresse demandée (voir
`bat32_restore.py`). La relecture de contrôle se fait après un `TARGET RESET`,
sinon le buffer du contrôleur flash peut confirmer une valeur que la cellule ne
porte pas.

⚠ **`0x00500004` porte OCDM, et `0x00500005` bit 0 porte BTEN** — les octets qui
décident du niveau de protection. `OCDM = 0x3C` **ferme la puce en Level 2, sans
retour possible, même par chip erase**. Ce mot est donc **exclu de la
restauration par défaut** (`--include-ocdm` pour l'inclure), et l'écriture d'un
`0x3C` à cette adresse est refusée sans échappatoire.

★ **La data flash est VIVANTE.** Mesure le 2026-09-02 : **12 octets ont changé
en dix minutes** de fonctionnement normal — des compteurs (`0x0050000C`,
`0x005000F0`, `0x00500204`, `0x005002E8`) et un enregistrement journalisé en
`0x00500453`. Deux conséquences :

- une sauvegarde est un **instantané** : la prendre juste avant l'opération, et
  ne pas s'étonner qu'elle diffère d'un dump plus ancien ;
- certains de ces incréments remettent des bits à 1 (`0x08` → `0x09`), donc le
  capteur **efface lui-même des pages**. Restaurer une image *antérieure* sur une
  puce qui a continué à tourner se heurtera au refus « bits 0->1 » : c'est
  normal. Le cas d'usage réel du restore est la **puce fraîchement effacée**
  (tout à `0xFF`), où toute valeur est programmable.

⚠ **Le chemin d'écriture n'a jamais été validé sur la data flash** : le firmware
n'y applique aucune restriction et `swd_bat32_flash_program()` est la séquence
du driver fondeur, mais seule la code flash a été exercée. Le restore commence
donc par écrire **un seul bloc témoin**, le vérifie après reset, et n'écrit la
suite que si ce témoin a pris.

Prerequis : pip install pyserial ; firmware raiden-pico >= v0.11.
"""
import argparse
import hashlib
import os
import sys
import time
from datetime import datetime

try:
    import serial
except ImportError:
    sys.exit("pyserial manquant : pip install pyserial")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bat32_restore import BLOCK, blocks_to_write, connect_and_halt, read_region, send

DATA_BASE = 0x00500000
DATA_SIZE = 1536
OCDM_ADDR = 0x00500004      # OCDM (octet 0) + BTEN (bit 0 de l'octet suivant)
OCDM_LEVEL2 = 0x3C          # la valeur qui ferme definitivement la puce


def do_backup(ser, args):
    print("lecture (coeur halte)...")
    first = read_region(ser, args.base, args.length)
    if first is None:
        return 1
    print("seconde passe de verification...")
    second = read_region(ser, args.base, args.length)
    if second is None:
        return 1
    if first != second:
        bad = [i for i in range(len(first)) if first[i] != second[i]]
        print(f"REFUS : les deux passes different sur {len(bad)} octets "
              f"(1er a 0x{args.base + bad[0]:08X}) — sauvegarde non fiable, "
              f"ne pas s'en servir comme reference")
        return 1
    path = args.out or os.path.join(
        os.getcwd(), f"bat32_data_flash.{datetime.now():%Y%m%d_%H%M%S}.bin")
    with open(path, "wb") as f:
        f.write(first)
    md5 = hashlib.md5(first).hexdigest()
    print(f"OK : {len(first)} octets @ 0x{args.base:08X} -> {path}")
    print(f"md5 {md5}")
    if args.base <= OCDM_ADDR < args.base + args.length:
        ocdm = first[OCDM_ADDR - args.base]
        print(f"OCDM (0x{OCDM_ADDR:08X}) = 0x{ocdm:02X}"
              f"{'  ATTENTION : Level 2' if ocdm == OCDM_LEVEL2 else ''}")
    return 0


def do_restore(ser, args):
    ref = open(args.image, "rb").read()
    if len(ref) < args.length:
        print(f"REFUS : {args.image} ne fait que {len(ref)} octets, "
              f"il en faut {args.length}")
        return 1
    ref = ref[:args.length]
    print(f"image {args.image} : md5 {hashlib.md5(ref).hexdigest()}")

    print("lecture de l'etat courant (coeur halte)...")
    cur = read_region(ser, args.base, args.length)
    if cur is None:
        return 1
    print(f"puce : md5 {hashlib.md5(cur).hexdigest()}")
    if cur == ref:
        print("deja identique a l'image — rien a faire")
        return 0

    todo, impossible = blocks_to_write(cur, ref, args.base)
    ndiff = sum(1 for i in range(args.length) if cur[i] != ref[i])
    print(f"{ndiff} octets a corriger, {len(todo)} blocs de {BLOCK}")
    if impossible:
        print(f"REFUS : {len(impossible)} blocs exigent des bits 0->1 "
              f"(1er a 0x{impossible[0]:08X}). La programmation ne fait que "
              f"1->0 ; il faut un erase prealable, et ce firmware n'expose que "
              f"le chip erase (qui emporterait aussi la code flash).")
        print("  Attendu si la puce a tourne depuis la sauvegarde : la data "
              "flash est vivante (compteurs, journal) et le capteur y efface "
              "des pages. Le restore vise une puce fraichement effacee.")
        return 1

    # Le mot OCDM/BTEN decide du niveau de protection : hors de la restauration
    # par defaut, et jamais de 0x3C (Level 2, irreversible).
    ocdm_off = OCDM_ADDR - args.base
    kept = []
    for addr, data in todo:
        off = addr - args.base
        if off <= ocdm_off < off + len(data):
            if ref[ocdm_off] == OCDM_LEVEL2:
                print(f"REFUS ABSOLU : l'image porte OCDM = 0x{OCDM_LEVEL2:02X} a "
                      f"0x{OCDM_ADDR:08X}, ce qui fermerait la puce en Level 2 "
                      f"sans retour possible.")
                return 1
            if not args.include_ocdm:
                print(f"  bloc 0x{addr:08X} ecarte : il contient OCDM/BTEN "
                      f"(0x{OCDM_ADDR:08X}). --include-ocdm pour le forcer.")
                continue
        kept.append((addr, data))
    todo = kept
    if not todo:
        print("plus rien a ecrire apres exclusion du bloc OCDM/BTEN")
        return 0

    if not args.confirm:
        print("diff seulement (ajouter --confirm pour ecrire)")
        for addr, _ in todo:
            print(f"  ecrirait 0x{addr:08X}")
        return 0

    # --- bloc temoin : le chemin d'ecriture data flash n'est pas valide ---
    probe_addr, probe_data = todo[0]
    print(f"bloc temoin 0x{probe_addr:08X} (le chemin data flash n'a jamais "
          f"ete exerce)...")
    out = send(ser, f"SWD BAT32 WRITE 0x{probe_addr:X} {probe_data.hex().upper()} "
                    f"CONFIRM", timeout=90, await_marker=True)
    if "OK:" not in out:
        print(f"  ECHEC : {out.strip()[-200:]}")
        return 1
    if not connect_and_halt(ser, args.speed, reset=True):
        return 1
    check = read_region(ser, probe_addr, len(probe_data))
    if check is None:
        return 1
    if check != probe_data:
        bad = [i for i in range(len(probe_data)) if check[i] != probe_data[i]]
        print(f"ARRET : le bloc temoin n'a pas pris ({len(bad)} octets, 1er a "
              f"0x{probe_addr + bad[0]:08X}). La data flash ne se programme pas "
              f"comme la code flash avec cette sequence — rien d'autre n'a ete "
              f"ecrit.")
        return 1
    print("  temoin verifie apres reset : le chemin data flash fonctionne")

    for n, (addr, data) in enumerate(todo[1:], 2):
        out = send(ser, f"SWD BAT32 WRITE 0x{addr:X} {data.hex().upper()} CONFIRM",
                   timeout=90, await_marker=True)
        if "OK:" not in out:
            print(f"  ECHEC a 0x{addr:08X} : {out.strip()[-200:]}")
            return 1
        print(f"  {n}/{len(todo)} blocs", flush=True)

    print("reset, puis relecture de verification...")
    if not connect_and_halt(ser, args.speed, reset=True):
        return 1
    after = read_region(ser, args.base, args.length)
    if after is None:
        return 1
    print(f"puce apres restauration : md5 {hashlib.md5(after).hexdigest()}")
    rest = [i for i in range(args.length) if after[i] != ref[i]]
    if not rest:
        print("RESTAURATION COMPLETE ET VERIFIEE")
        return 0
    if not args.include_ocdm and all(i in (ocdm_off, ocdm_off + 1) for i in rest):
        print(f"RESTAURATION VERIFIEE hors OCDM/BTEN "
              f"({len(rest)} octet(s) laisses volontairement)")
        return 0
    print(f"INCOMPLETE : {len(rest)} octets encore differents, "
          f"1er a 0x{args.base + rest[0]:08X}")
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", default="/dev/ttyACM0")
    common.add_argument("--speed", type=int, default=4)
    common.add_argument("--base", type=lambda x: int(x, 0), default=DATA_BASE)
    common.add_argument("--len", dest="length", type=lambda x: int(x, 0),
                        default=DATA_SIZE)
    common.add_argument("--no-reset", action="store_true",
                        help="ne pas pulser TARGET RESET avant SWD CONNECT")

    b = sub.add_parser("backup", parents=[common],
                       help="sauvegarder la data flash (lecture double)")
    b.add_argument("--out", help="fichier de sortie (defaut : horodate)")

    r = sub.add_parser("restore", parents=[common],
                       help="restaurer la data flash depuis une image")
    r.add_argument("--image", required=True)
    r.add_argument("--confirm", action="store_true",
                   help="ecrire reellement ; sans lui, diff seulement")
    r.add_argument("--include-ocdm", action="store_true",
                   help="inclure le bloc portant OCDM/BTEN (0x00500004). "
                        "Exclu par defaut : ces octets decident du niveau de "
                        "protection de la puce.")
    args = ap.parse_args()

    # serial_for_url accepte /dev/ttyACM0 comme socket://host:port
    with serial.serial_for_url(args.port, 115200, timeout=0.2,
                               write_timeout=5) as ser:
        time.sleep(0.6)
        if not connect_and_halt(ser, args.speed, reset=not args.no_reset):
            return 1
        return do_backup(ser, args) if args.cmd == "backup" else do_restore(ser, args)


if __name__ == "__main__":
    sys.exit(main())
