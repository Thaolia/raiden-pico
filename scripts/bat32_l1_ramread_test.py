#!/usr/bin/env python3
"""Test du contournement du Level 1 par `SWD BAT32 RAMREAD` — BAT32G135.

C'est l'expérience de la priorité −1 du protocole (doc 08 §9) et la question
ouverte n°6 : **le blocage flash du Level 1 vise-t-il seulement le debugger,
ou aussi le cœur ?** RAMREAD fait lire la flash par le cœur lui-même (payload
Thumb injecté en SRAM). Au Level 0 le mécanisme est validé au md5 près sur
64 Ko ; au Level 1 il n'a jamais été tenté. Si ça passe, le Level 1 tombe
sans injection de faute et toute la campagne de glitch devient inutile.

Ce script n'écrit JAMAIS dans la flash — sauf derrière `--arm`, qui est la
seule opération destructive et qui exige une sauvegarde relue sur la puce.

────────────────────────────────────────────────────────────────────────────
★ Le Level 1 est EXIGÉ, et vérifié deux fois
────────────────────────────────────────────────────────────────────────────

Le script mesure le niveau en phase 1 et **refuse d'aller plus loin** si la
puce n'est pas au Level 1, puis **re-mesure juste avant la passe d'essai** —
entre les deux il y a eu un dump SRAM, parfois un armement et un reset. La
raison n'est pas la prudence mais la logique : au Level 0 le debugger a déjà
le droit de lire la flash, donc **un RAMREAD réussi n'y prouve rien du
bypass**. Un résultat obtenu sur une puce non protégée est un faux positif.

Le niveau est établi par ce que la puce FAIT (la flash faute, la SRAM répond),
jamais par `SWD OPT` : ses option bytes vivent en code flash, donc illisibles
précisément au Level 1 (07 §2bis.1).

  Puce au Level 1                 →  TEST DU BYPASS (exige `--ref`)
      Au Level 1 `SWD READ` faute : il n'y a plus de second chemin, donc la
      seule comparaison possible est contre une image prise au Level 0.

  Puce au Level 0, avec `--arm`   →  SÉQUENCE COMPLÈTE (destructif)
      Relit la flash sur la puce, la compare à la sauvegarde fournie, arme le
      Level 1, puis teste. La référence de comparaison est la relecture qu'il
      vient de faire : même puce, même instant, même chemin.

  Puce au Level 0, `--allow-level0` →  VALIDATION du harnais (aucun risque)
      La seule façon d'attaquer une puce non protégée, et elle est explicite.
      Au Level 0 `SWD READ` fournit encore un second chemin : on exige
      l'égalité RAMREAD == SWD READ. Ça n'éprouve pas le bypass — ça éprouve
      *le harnais* : parsing, découpage, classement des messages d'échec. À
      faire AVANT d'armer, sinon un bug de regex se lira « le bypass a échoué ».

  Puce au Level 0, sans option    →  REFUS, avec les trois suites possibles.

────────────────────────────────────────────────────────────────────────────
Trois pièges que ce script prend en charge — ne pas les refaire à la main
────────────────────────────────────────────────────────────────────────────

★ **RAMREAD écrase la SRAM cible `0x20000000`-`0x2000101F`.** C'est exactement
  où vit le `.data` recopié depuis la flash à chaque boot — c'est-à-dire du
  contenu d'origine flash lisible au Level 1 **sans aucune écriture**. C'est le
  repli si le bypass échoue, et RAMREAD le détruit. Le script dumpe donc la
  SRAM AVANT la première passe, systématiquement, et refuse de continuer s'il
  n'y arrive pas.

  ⚠ **Et un reset ne rend pas tout.** Mesuré sur le banc le 2026-09-06 : après
  le `TARGET RESET` de sortie, **93 %** seulement de la zone revient à sa valeur
  d'avant — ~282 octets gardent ce que le payload y a laissé (le copieur lui-même
  n'était réécrit qu'à moitié : ses deux premiers mots remis à zéro, les deux
  suivants intacts). Le boot n'initialise que ce que `.data`/`.bss` couvrent ;
  le reste n'appartient à personne et garde la dernière écriture. **L'écrasement
  est donc partiellement irréversible** : le dump de la phase 2 est la seule
  copie fidèle, et il n'y a pas de seconde chance de le prendre.

★ **`SWD OPT` ne peut pas servir d'oracle au Level 1.** Les option bytes
  vivent en code flash, mesurée illisible au Level 1 (07 §2bis.1) : la commande
  répondra « Could not determine protection level » précisément quand on a
  besoin de la réponse. L'oracle est donc *fonctionnel* — la flash faute et la
  SRAM répond — jamais déclaratif. `SWD OPT` reste affiché, à titre indicatif.

★ **Le sticky error est déjà géré par le firmware.** Une lecture flash en FAULT
  verrouille `STICKYERR` et empoisonne toutes les transactions AP suivantes,
  mais `command_parser.c` fait un `swd_clear_errors()` (DP_ABORT) en entrée de
  *chaque* sous-commande `SWD` sauf RACE/SPEED/PHY/BENCH, et
  `swd_bat32_ram_read()` en fait un lui-même avant son halt. Rien à ajouter
  côté hôte : ne pas réintroduire de DP_ABORT manuel ici.

★ La cible est **remise en marche en sortant** (`TARGET RESET`), sur tous les
chemins : la phase 2 halte le cœur et RAMREAD lui écrase 4 Ko de SRAM ; ni
l'un ni l'autre ne doit lui survivre. La flash n'est jamais touchée (hors
`--arm`), mais la SRAM ne revient qu'à 93 % — voir l'avertissement ci-dessus.

Exemples :
    # 1. Éprouver le harnais au Level 0 (à faire en premier, sans risque)
    python3 scripts/bat32_l1_ramread_test.py --allow-level0

    # 2. La séquence complète, sauvegarde en main
    python3 scripts/bat32_dump.py --out-dir /tmp/backup_avant_l1
    python3 scripts/bat32_l1_ramread_test.py --arm \\
        --backup-code /tmp/backup_avant_l1/bat32_code_flash.bin \\
        --backup-data /tmp/backup_avant_l1/bat32_data_flash.bin

    # 3. Puce déjà au Level 1
    python3 scripts/bat32_l1_ramread_test.py \\
        --ref assets/bat32_dump_20260831/bat32_code_flash.bin --full

Prérequis : pip install pyserial ; firmware raiden-pico >= v0.13.
⚠ Firmware < v0.13 : `RAMREAD` y ré-arme TAR une seule fois, donc tout
transfert franchissant une frontière de 1 Ko rend des données fausses qui se
répètent à partir de l'offset 0x3E0. Ce symptôme est un bug de firmware, pas
un résultat de Level 1 — vérifier `VERSION` avant d'interpréter quoi que ce soit.
"""
import argparse
import hashlib
import os
import re
import sys
import time
from datetime import datetime

try:
    import serial
except ImportError:
    sys.exit("pyserial manquant : pip install pyserial")

# ── Cartographie de la cible (include/bat32_target.h) ──────────────────────
CODE_FLASH = 0x00000000
CODE_SIZE = 0x10000            # 64 Ko
DATA_FLASH = 0x00500000
DATA_SIZE = 0x600              # 1,5 Ko
SRAM_BASE = 0x20000000
SRAM_SIZE = 0x2000             # 8 Ko
SRAM_PROBE = 0x20000008        # témoin du §2bis.1bis. SRAM VIVANTE : sa valeur change
                               # (0xE864F825 au doc, 0xE8607825 relu le 2026-09-06) —
                               # seule sa LISIBILITÉ fait oracle, jamais son contenu.

# Zone que le payload RAMREAD écrase (swd.c : PAYLOAD_ADDR/BUF + 4 Ko)
PAYLOAD_LO, PAYLOAD_HI = 0x20000000, 0x2000101F

MAX_WORDS = 1024               # plafond imposé par la CLI : 4 Ko par passe


class Console:
    """Journal double : terminal + fichier daté."""

    def __init__(self, path):
        self.fh = open(path, "w", encoding="utf-8") if path else None
        self.path = path

    def __call__(self, msg="", end="\n"):
        print(msg, end=end, flush=True)
        if self.fh:
            self.fh.write(msg + end)
            self.fh.flush()

    def phase(self, num, title):
        self("")
        self(f"── Phase {num} — {title} " + "─" * max(0, 56 - len(title)))


def send(ser, cmd, settle=1.0, timeout=120.0):
    """Envoie une commande CLI et rend tout ce qui revient jusqu'au silence."""
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
    # Placement par ADRESSE, jamais par concaténation : une ligne perdue coûte
    # ses propres octets au lieu de décaler tout ce qui suit.
    if any(base + 4 * i not in got for i in range(nwords)):
        return None
    return b"".join(got[base + 4 * i].to_bytes(4, "little") for i in range(nwords))


def parse_bytes(txt, base, nbytes):
    """Sortie SWD READ : hexdump 16 octets par ligne, puis ASCII."""
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


# ── Oracle de niveau : fonctionnel, jamais déclaratif ──────────────────────

def read_word(ser, addr):
    """(succès, valeur|None, ack|None). Un FAULT est un résultat, pas une panne."""
    out = send(ser, f"SWD READ 0x{addr:X} 1", settle=0.4, timeout=25)
    blob = parse_bytes(out, addr, 4)
    if blob is not None:
        return True, int.from_bytes(blob, "little"), None
    m = re.search(r"Read failed at 0x[0-9A-Fa-f]+ \(ACK=0x([0-9A-Fa-f]+)\)", out)
    return False, None, (int(m.group(1), 16) if m else None)


def probe_level(ser, log):
    """Classe la puce d'après ce qu'elle FAIT : flash muette + SRAM vivante = L1."""
    f_ok, f_val, f_ack = read_word(ser, CODE_FLASH)
    s_ok, s_val, s_ack = read_word(ser, SRAM_PROBE)

    log(f"  flash 0x{CODE_FLASH:08X} : " +
        (f"lue = 0x{f_val:08X}" if f_ok else f"FAULT (ACK=0x{f_ack:X})"
         if f_ack is not None else "illisible (pas d'ACK rapporté)"))
    log(f"  SRAM  0x{SRAM_PROBE:08X} : " +
        (f"lue = 0x{s_val:08X}" if s_ok else f"FAULT (ACK=0x{s_ack:X})"
         if s_ack is not None else "illisible (pas d'ACK rapporté)"))

    if f_ok and s_ok:
        return "L0"
    if not f_ok and s_ok:
        return "L1"
    if not f_ok and not s_ok:
        return "MUET"          # ni flash ni SRAM : ce n'est pas un niveau
    return "ANORMAL"           # flash lisible mais pas la SRAM : incohérent


LEVEL_HELP = {
    "MUET": (
        "Ni la flash ni la SRAM ne répondent. Ce n'est PAS le Level 1 (au L1 la\n"
        "  SRAM reste grande ouverte, mesuré §2bis.1bis). Causes probables, dans\n"
        "  l'ordre : pas de TARGET RESET avant SWD CONNECT (la cible imite alors\n"
        "  une puce verrouillée) ; SWD SPEED 0 sur un câblage volant ; câblage ou\n"
        "  alimentation. Vérifier avec SWD IDCODE avant de conclure au Level 2."),
    "ANORMAL": (
        "La flash répond mais pas la SRAM — l'inverse de tout ce qui a été mesuré\n"
        "  sur cette puce. Lien instable : ralentir l'horloge (--speed 8) et refaire."),
}


# ── Classement des échecs RAMREAD — c'est la valeur de ce script ───────────

def classify_ramread(out):
    """(code, verdict) d'après le message [BAT32-RAM] du firmware."""
    if "SRAM not writable" in out:
        return "SRAM_RO", (
            "La SRAM n'est pas ÉCRIVABLE au Level 1 (elle est lisible — §2bis.1bis).\n"
            "  La protection couvre donc plus que la lecture flash : aucun payload ne\n"
            "  peut être injecté. ⇒ Voie §2bis.5 fermée.\n"
            "  REPLI IMMÉDIAT, sans aucune écriture : le dump SRAM déjà pris en phase 3\n"
            "  contient le `.data` recopié depuis la flash au boot. C'est une fuite de\n"
            "  contenu d'origine flash — plus petite, mais gratuite et déjà en main.")
    if "core halt failed" in out:
        return "NO_HALT", (
            "Le cœur refuse de halter. Contredit la mesure du §2bis.1bis (halt réussi\n"
            "  au Level 1 une fois le sticky error effacé) — donc suspecter d'abord le\n"
            "  lien, pas la puce : refaire avec --speed 8, et vérifier SWD IDCODE.")
    if "core register write failed" in out:
        return "NO_DCRSR", (
            "L'écriture de PC/SP via DCRSR/DCRDR est refusée. Le payload est en SRAM\n"
            "  mais on ne peut pas y amener le cœur. ⇒ Voie §2bis.5 fermée par le\n"
            "  contrôle du cœur, pas par la flash. Même repli `.data` que ci-dessus.")
    if "payload readback mismatch" in out:
        return "SRAM_MISMATCH", (
            "Le payload s'écrit mais se relit différent. Ce n'est pas un refus franc :\n"
            "  soit la SRAM est en écriture partielle, soit le lien corrompt. Refaire à\n"
            "  --speed 8 avant d'en tirer une conclusion sur la protection.")
    if "resume failed" in out:
        return "NO_RESUME", (
            "Le cœur refuse de repartir : payload en place, registres écrits, mais le\n"
            "  `resume` est rejeté. Le debug du cœur reste donc sous contrôle du Level 1\n"
            "  au-delà du halt. ⇒ Voie §2bis.5 fermée sur le contrôle d'exécution.\n"
            "  Même repli `.data` que pour SRAM_RO : le dump SRAM de la phase 2 le contient.")
    if "is not word-aligned" in out:
        return "MISALIGNED", (
            "Adresse source non alignée sur 4 — refusée par le firmware AVANT tout accès\n"
            "  à la puce. C'est une erreur d'appel, pas un résultat : corriger --probe-addr.")

    m = re.search(r"halted at PC=0x([0-9A-Fa-f]{8}), not the trailing BKPT "
                  r"\(DFSR=0x([0-9A-Fa-f]{8})\)", out)
    if m:
        pc, dfsr = int(m.group(1), 16), int(m.group(2), 16)
        if dfsr & (1 << 3):        # VCATCH : hard fault attrapé par VC_HARDERR
            return "CORE_FAULT", (
                f"Le cœur a levé un HARD FAULT (DFSR=0x{dfsr:08X}, bit VCATCH), PC=0x{pc:08X}.\n"
                "  ★ C'EST LE RÉSULTAT DÉCISIF, ET IL EST NÉGATIF : le `ldr r3,[r0]` du\n"
                "  copieur a fauté en lisant la flash. Le contrôleur flash bloque donc\n"
                "  AUSSI les accès du CŒUR, pas seulement ceux du debugger — l'hypothèse\n"
                "  qui fonde toute la voie §2bis.5. ⇒ Level 1 non contournable par SRAM ;\n"
                "  le glitch de la fenêtre A redevient la seule voie.\n"
                "  Avant de le graver : refaire une passe avec --probe-addr sur une\n"
                "  adresse SRAM connue (ex. 0x20001800). Si RAMREAD réussit sur SRAM et\n"
                "  faute sur flash, le diagnostic est confirmé ; s'il faute aussi sur\n"
                "  SRAM, c'est le payload ou le lien, pas la protection.")
        return "PC_ELSEWHERE", (
            f"Le cœur s'est arrêté à PC=0x{pc:08X}, pas sur le BKPT final "
            f"(DFSR=0x{dfsr:08X}, bit VCATCH absent).\n"
            "  Ce n'est pas un fault de lecture flash : le cœur a fini ailleurs. Vérifier\n"
            "  qu'aucun autre BKPT/point d'arrêt ne traîne (SWD BPTEST), et refaire.")

    if "not the trailing BKPT" in out:
        # Même message, DFSR non capturé (sortie tronquée par le lien série). On ne
        # devine pas le bit VCATCH : c'est lui qui sépare « le cœur a fauté sur la
        # flash » de « le cœur a fini ailleurs », deux conclusions opposées.
        m = re.search(r"PC=0x([0-9A-Fa-f]{8})", out)
        pc = f"0x{m.group(1)}" if m else "inconnu"
        return "BKPT_MANQUE", (
            f"Le cœur s'est arrêté hors du BKPT final (PC={pc}) mais le DFSR n'a pas pu\n"
            "  être lu dans la réponse — or c'est son bit 3 (VCATCH) qui tranche entre un\n"
            "  HARD FAULT sur la lecture flash (⇒ le Level 1 bloque aussi le cœur, voie\n"
            "  fermée) et un simple arrêt ailleurs (⇒ rien de conclu). NE PAS conclure :\n"
            "  refaire la passe, au besoin avec --speed 8 pour une sortie série complète.")
    if "PC unreadable after halt" in out:
        return "NO_PC", (
            "PC illisible après le halt : le firmware REFUSE de rendre le tampon, et il a\n"
            "  raison — au Level 1 ce tampon contiendrait plausiblement le `.data` d'origine\n"
            "  flash que le payload n'a jamais lu. Un succès apparent serait un faux positif.")
    if "core LOCKED UP" in out:
        return "LOCKUP", (
            "Le cœur est parti en LOCKUP. Typiquement un fault pendant le fault (pile\n"
            "  invalide). Vérifier que SP=0x20002000 tombe bien dans la SRAM de CE boîtier.")
    if "core still running" in out:
        return "NO_BKPT", (
            "Le cœur tourne toujours après 500 ms : il n'a jamais atteint le BKPT.\n"
            "  Soit il n'exécute pas depuis la SRAM, soit il n'est jamais parti. Le Level 0\n"
            "  a prouvé que cet ARMv6-M exécute bien depuis la SRAM — donc si la phase de\n"
            "  validation est passée, c'est un effet du Level 1.")
    return "INCONNU", (
        "Échec non reconnu — lire le journal brut ci-dessus. Si aucune ligne\n"
        "  [BAT32-RAM] n'apparaît, la commande n'a pas atteint le firmware : vérifier\n"
        "  VERSION >= v0.13 et que TARGET BAT32 est bien sélectionné.")


# ── Lectures ───────────────────────────────────────────────────────────────

def read_via_debugger(ser, base, total, log, label):
    """Lecture par le debugger (SWD READ). None si un FAULT survient."""
    out_buf = bytearray()
    for off in range(0, total, MAX_WORDS * 4):
        nb = min(MAX_WORDS * 4, total - off)
        addr = base + off
        out = send(ser, f"SWD READ 0x{addr:X} {nb // 4}", timeout=180)
        blob = None if "ERROR" in out else parse_bytes(out, addr, nb)
        if blob is None:
            log(f"  ✗ SWD READ a échoué à 0x{addr:08X} ({label})")
            log("    " + out.strip()[-300:].replace("\n", "\n    "))
            return None
        out_buf += blob
        log(f"  0x{addr:08X} +{nb} ok ({label})")
    return bytes(out_buf)


def read_via_core(ser, base, total, log):
    """Lecture par le cœur (RAMREAD). (données|None, code, verdict)."""
    out_buf = bytearray()
    for off in range(0, total, MAX_WORDS * 4):
        nw = min(MAX_WORDS * 4, total - off) // 4
        addr = base + off
        out = send(ser, f"SWD BAT32 RAMREAD 0x{addr:X} {nw}", timeout=180)
        blob = None if "ERROR" in out else parse_words(out, addr, nw)
        if blob is None:
            log(f"  ✗ RAMREAD a échoué à 0x{addr:08X}")
            log("    " + out.strip()[-600:].replace("\n", "\n    "))
            code, verdict = classify_ramread(out)
            return None, code, verdict
        out_buf += blob
        log(f"  0x{addr:08X} +{nw * 4} ok (cœur)")
    return bytes(out_buf), "OK", None


def diff_report(a, b, base, log, label_a="cœur", label_b="réf"):
    diffs = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    if not diffs:
        return
    log(f"  {len(diffs)} octets divergents sur {min(len(a), len(b))}")
    zones, start, prev = [], diffs[0], diffs[0]
    for i in diffs[1:]:
        if i - prev > 16:
            zones.append((start, prev))
            start = i
        prev = i
    zones.append((start, prev))
    for lo, hi in zones[:10]:
        log(f"    0x{base + lo:08X}-0x{base + hi:08X}  "
            f"{label_a}={a[lo:lo + 4].hex()} {label_b}={b[lo:lo + 4].hex()}")
    if len(zones) > 10:
        log(f"    ... {len(zones) - 10} zones de plus")


# ── Armement (seul chemin destructif) ──────────────────────────────────────

def do_arm(ser, args, log, stamp):
    """Relit la puce, compare aux sauvegardes, puis arme. Rend la référence lue."""
    log("  Relecture de la flash SUR LA PUCE — la sauvegarde fournie ne suffit pas :")
    log("  la data flash est vivante (12 octets de dérive en 10 min, compteurs à")
    log("  0x0050000C / 0x005000F0 / 0x00500204 / 0x005002E8).")

    code = read_via_debugger(ser, CODE_FLASH, CODE_SIZE, log, "code flash")
    if code is None:
        log("  ✗ Impossible de relire la code flash — RIEN n'est armé.")
        return None
    data = read_via_debugger(ser, DATA_FLASH, DATA_SIZE, log, "data flash")
    if data is None:
        log("  ✗ Impossible de relire la data flash — RIEN n'est armé.")
        return None

    log(f"  code flash lue : md5 {hashlib.md5(code).hexdigest()}")
    log(f"  data flash lue : md5 {hashlib.md5(data).hexdigest()}")

    # ★ La relecture qu'on vient de faire EST la sauvegarde la plus fraîche qui
    # existe. On l'écrit avant toute comparaison : quoi qu'il arrive ensuite,
    # l'état exact de la puce à l'instant de l'armement est sur le disque.
    for blob, tag in ((code, "code_flash"), (data, "data_flash")):
        path = f"{args.out_dir}/bat32_{tag}_avant_arm_{stamp}.bin"
        open(path, "wb").write(blob)
        log(f"  ✓ sauvegarde fraîche → {path}")

    for blob, path, name, size in ((code, args.backup_code, "code flash", CODE_SIZE),
                                   (data, args.backup_data, "data flash", DATA_SIZE)):
        saved = open(path, "rb").read()
        if len(saved) != size:
            log(f"  ✗ {path} fait {len(saved)} octets, il en faut {size}. RIEN n'est armé.")
            return None
        if saved == blob:
            log(f"  ✓ {name} identique à {path}")
            continue

        base = CODE_FLASH if name == "code flash" else DATA_FLASH
        drift = sum(1 for a, b in zip(blob, saved) if a != b)
        if name == "code flash":
            # La code flash ne bouge pas toute seule. Un écart ici veut dire que la
            # sauvegarde n'est pas celle de cette puce — et c'est CELLE-LÀ que le chip
            # erase détruira, donc pas de tolérance.
            log(f"  ✗ La code flash de la puce DIFFÈRE de {path} ({drift} octets).")
            diff_report(blob, saved, base, log, "puce", "fichier")
            log("  ⇒ La code flash ne dérive pas d'elle-même : cette sauvegarde n'est pas")
            log("    celle de cette puce. Reprendre un dump frais (bat32_dump.py, cœur")
            log("    halté) avant toute chose. RIEN n'est armé.")
            return None

        # La data flash, elle, est VIVANTE : compteurs et journal bougent en
        # fonctionnement (12 octets en 10 minutes, mesuré le 2026-09-02). Exiger
        # l'égalité ferait échouer l'armement pour la raison la plus normale du monde.
        log(f"  ⚠ data flash : {drift} octets de dérive avec {path}")
        diff_report(blob, saved, base, log, "puce", "fichier")
        if drift > args.data_drift_max:
            log(f"  ✗ Dérive supérieure au seuil ({args.data_drift_max} octets) : ce n'est")
            log("    plus du vieillissement de compteurs mais un écart structurel — soit la")
            log("    sauvegarde n'est pas celle de cette puce, soit la lecture est douteuse.")
            log("    Relever --data-drift-max si la dérive est bien comprise. RIEN n'est armé.")
            return None
        log("    ⇒ cohérent avec une data flash vivante (compteurs à 0x0050000C /")
        log("      0x005000F0 / 0x00500204 / 0x005002E8, journal à 0x00500453).")
        log("      C'est la sauvegarde fraîche écrite ci-dessus qui fait foi, pas ce fichier.")

    log("")
    log("  ⚠ CE QUE COÛTE L'ARMEMENT, exactement :")
    log("    • la SEULE sortie du Level 1 est le chip erase, qui DÉTRUIT la code flash ;")
    log("    • la restauration des 64 Ko prend ~70 s à 2500 kHz, puis doit être relue")
    log("      APRÈS un reset pour être crue ;")
    log("    • le chip erase ÉPARGNE la data flash (mesuré 2026-09-02) : l'appairage")
    log("      survit. Mais les sources divergent sur la restauration de 0x00500000 —")
    log("      scripts/README.md la donne validée le 02/09, le README du dump")
    log("      assets/bat32_dump_20260902 la donne jamais exercée. Ne pas parier :")
    log("      `python3 scripts/bat32_dataflash.py backup` juste avant, la data flash")
    log("      est vivante (12 octets de dérive en 10 minutes) ;")
    log("    • `SWD BAT32 DISARM` ne marche PAS au Level 1 : l'écriture est refusée.")
    log("")
    try:
        rep = input("  Taper exactement ARMER pour continuer, autre chose pour annuler : ")
    except EOFError:
        rep = ""
    if rep.strip() != "ARMER":
        log("  Annulé — rien n'a été armé.")
        return None

    out = send(ser, "SWD BAT32 ARM CONFIRM", settle=0.6, timeout=60)
    log("  " + out.strip().replace("\n", "\n  "))
    if "ERROR" in out:
        log("  ✗ L'armement a échoué.")
        return None
    send(ser, "TARGET RESET", settle=0.6, timeout=30)
    send(ser, "SWD CONNECT", settle=0.6, timeout=30)
    log("  Level 1 armé, cible resetée.")
    return code


# ── Programme principal ────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyACM0",
                    help="port du raiden-pico (accepte aussi socket://hote:port)")
    ap.add_argument("--speed", type=int, default=4,
                    help="SWD SPEED (défaut 4 ~125 kHz ; 0 ne marche pas sur ce banc)")
    ap.add_argument("--ref", help="image code flash de référence, prise au Level 0 "
                                  "(obligatoire si la puce est déjà au Level 1)")
    ap.add_argument("--probe-addr", type=lambda x: int(x, 0), default=CODE_FLASH,
                    help="adresse de la passe d'essai (défaut 0x0, doit être alignée "
                         "sur 4 ; une adresse SRAM sert à départager un CORE_FAULT)")
    ap.add_argument("--probe-words", type=int, default=MAX_WORDS,
                    help=f"mots de la passe d'essai (défaut {MAX_WORDS} = 4 Ko, maximum CLI)")
    ap.add_argument("--full", action="store_true",
                    help="après un essai concluant, dumper les 64 Ko par le cœur")
    ap.add_argument("--arm", action="store_true",
                    help="DESTRUCTIF — armer le Level 1 (exige --backup-code/--backup-data)")
    ap.add_argument("--backup-code", help="sauvegarde code flash 64 Ko (requise par --arm)")
    ap.add_argument("--backup-data", help="sauvegarde data flash 1536 o (requise par --arm)")
    ap.add_argument("--out-dir", default=".", help="où déposer dumps et journal")
    ap.add_argument("--data-drift-max", type=int, default=64,
                    metavar="N",
                    help="dérive tolérée sur la data flash face à --backup-data lors "
                         "d'un --arm (défaut 64 octets). Elle est VIVANTE : 12 octets "
                         "ont bougé en 10 minutes le 2026-09-02. La code flash, elle, "
                         "doit correspondre exactement.")
    ap.add_argument("--allow-level0", action="store_true",
                    help="autoriser la passe alors que la puce N'EST PAS au Level 1. "
                         "Sans cette option le script refuse d'attaquer une puce non "
                         "protégée : au Level 0 un RAMREAD réussi ne prouve rien du "
                         "bypass. À n'utiliser que pour éprouver le harnais.")
    ap.add_argument("--no-reset", action="store_true",
                    help="ne pas pulser TARGET RESET avant SWD CONNECT (déconseillé)")
    args = ap.parse_args()

    if args.probe_addr & 3:
        ap.error(f"--probe-addr 0x{args.probe_addr:X} doit être aligné sur 4 : le `ldr` du "
                 "copieur faute sinon, et l'échec se lirait comme un refus du Level 1")
    if not 1 <= args.probe_words <= MAX_WORDS:
        ap.error(f"--probe-words doit être entre 1 et {MAX_WORDS} (plafond de la CLI)")
    if args.arm and not (args.backup_code and args.backup_data):
        ap.error("--arm exige --backup-code ET --backup-data : on n'arme pas le Level 1 "
                 "sans une sauvegarde relue sur la puce")

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = Console(f"{args.out_dir}/bat32_l1_ramread_{stamp}.log")
    log(f"Test RAMREAD au Level 1 — {datetime.now():%Y-%m-%d %H:%M:%S}")
    log(f"port={args.port} speed={args.speed} "
        f"probe=0x{args.probe_addr:08X}+{args.probe_words * 4}o")

    reference = None
    if args.ref:
        reference = open(args.ref, "rb").read()
        log(f"référence {args.ref} : {len(reference)} octets, "
            f"md5 {hashlib.md5(reference).hexdigest()}")

    with serial.serial_for_url(args.port, 115200, timeout=0.2, write_timeout=5) as ser:
        time.sleep(0.6)
        # La cible ressort de ce script telle qu'elle y est entrée : le halt de la
        # phase 2 et le payload que RAMREAD écrit en SRAM ne doivent pas lui survivre.
        core_touched = False
        try:

            # ── Phase 0 — préflight ───────────────────────────────────────────
            log.phase(0, "préflight")
            ver = send(ser, "VERSION", settle=0.4, timeout=15)
            log("  " + ver.strip().replace("\n", "\n  "))
            m = re.search(r"v(\d+)\.(\d+)", ver)
            if m and (int(m.group(1)), int(m.group(2))) < (0, 13):
                log("  ✗ Firmware < v0.13 : RAMREAD y ré-arme TAR une seule fois et rend des")
                log("    données fausses au-delà de 1 Ko. Flasher avant de tester quoi que ce soit.")
                return 1
            if not m:
                log("  ⚠ Version du firmware non reconnue — RAMREAD exige >= v0.13.")

            for cmd in (["TARGET BAT32", f"SWD SPEED {args.speed}"] +
                        ([] if args.no_reset else ["TARGET RESET"]) + ["SWD CONNECT"]):
                out = send(ser, cmd, settle=0.4, timeout=30)
                if "ERROR" in out:
                    log(f"  ✗ échec sur `{cmd}` : {out.strip()[-200:]}")
                    return 1
                log(f"  ✓ {cmd}")
            log("  " + send(ser, "SWD IDCODE", settle=0.4, timeout=20).strip().replace("\n", "\n  "))

            # ── Phase 1 — niveau réel, mesuré ─────────────────────────────────
            log.phase(1, "niveau de protection (oracle fonctionnel)")
            level = probe_level(ser, log)
            log(f"  ⇒ niveau mesuré : {level}")
            if level in LEVEL_HELP:
                log("  ✗ " + LEVEL_HELP[level])
                return 1
            opt = send(ser, "SWD OPT", settle=0.5, timeout=30)
            log("  SWD OPT (indicatif — illisible au Level 1, ne jamais s'y fier) :")
            log("    " + opt.strip().replace("\n", "\n    "))

            # Garde-fou : on n'attaque pas une puce dont on n'a pas établi qu'elle
            # est protégée. Un « succès » obtenu au Level 0 ne prouve rien du bypass
            # — le debugger y a déjà le droit de lire la flash.
            if level != "L1" and not args.arm and not args.allow_level0:
                log("")
                log(f"  ✗ La puce est au {level}, pas au Level 1 : rien à contourner ici.")
                log("    Un RAMREAD réussi au Level 0 ne dit RIEN du bypass — c'est le cas")
                log("    nominal, la protection n'est pas armée.")
                log("    Trois suites possibles :")
                log("      • éprouver le harnais sans risque    → --allow-level0")
                log("      • armer le Level 1 puis tester       → --arm --backup-code … "
                    "--backup-data …")
                log("      • armer à la main                    → SWD BAT32 ARM CONFIRM "
                    "puis TARGET RESET")
                return 1

            # ── Phase 2 — sauvegarde SRAM, AVANT le premier RAMREAD ───────────
            log.phase(2, "sauvegarde de la SRAM (obligatoire avant tout RAMREAD)")
            log(f"  RAMREAD écrase 0x{PAYLOAD_LO:08X}-0x{PAYLOAD_HI:08X}, où vit le `.data`")
            log("  recopié depuis la flash au boot — la fuite gratuite du Level 1.")
            # À partir d'ici la cible n'est plus dans son état nominal : le `finally`
            # ci-dessous lui doit un TARGET RESET, quel que soit le chemin de sortie.
            core_touched = True
            send(ser, "SWD HALT", settle=0.4, timeout=20)
            sram = read_via_debugger(ser, SRAM_BASE, SRAM_SIZE, log, "SRAM")
            if sram is None:
                log("  ✗ SRAM illisible : on n'écrase pas une SRAM qu'on n'a pas sauvegardée.")
                log("    (Au Level 1 elle DOIT être lisible — §2bis.1bis. Suspecter le lien.)")
                return 1
            sram_path = f"{args.out_dir}/bat32_sram_{level.lower()}_{stamp}.bin"
            open(sram_path, "wb").write(sram)
            log(f"  ✓ {len(sram)} octets → {sram_path} (md5 {hashlib.md5(sram).hexdigest()})")

            # ── Phase 3 — armement éventuel ───────────────────────────────────
            if args.arm:
                if level != "L0":
                    log.phase(3, "armement")
                    log(f"  ✗ --arm demandé mais la puce est déjà en {level}. Rien à armer.")
                    return 1
                log.phase(3, "armement du Level 1 (DESTRUCTIF)")
                reference = do_arm(ser, args, log, stamp)
                if reference is None:
                    return 1
                ref_path = f"{args.out_dir}/bat32_code_flash_ref_{stamp}.bin"
                open(ref_path, "wb").write(reference)
                log(f"  référence écrite : {ref_path} "
                    f"(md5 {hashlib.md5(reference).hexdigest()})")
                log("")
                log("  Re-mesure du niveau après armement :")
                level = probe_level(ser, log)
                log(f"  ⇒ niveau mesuré : {level}")
                if level != "L1":
                    log("  ✗ L'armement n'a pas produit un Level 1 mesurable. Arrêt.")
                    return 1

            # ── Choix du mode, d'après le niveau réel ─────────────────────────
            if level == "L0":
                mode = "validation"
                log.phase(4, "MODE VALIDATION (--allow-level0) — le harnais, PAS le bypass")
                log("  Au Level 0 `SWD READ` fournit un second chemin : on exige l'égalité")
                log("  RAMREAD == SWD READ. Un écart ici est un bug du script ou du lien —")
                log("  le corriger AVANT d'armer, sinon il se lira comme un échec du bypass.")
            else:
                mode = "bypass"
                log.phase(4, "MODE BYPASS (Level 1) — l'expérience proprement dite")
                if reference is None:
                    log("  ✗ Au Level 1 il n'y a plus de second chemin : sans --ref, un RAMREAD")
                    log("    réussi ne peut pas être distingué d'un tampon de SRAM périmé.")
                    log("    Relancer avec --ref <image code flash prise au Level 0>.")
                    return 1

            # ── Phase 5 — la passe d'essai ────────────────────────────────────
            log.phase(5, f"passe d'essai — {args.probe_words * 4} octets par le cœur")

            # Re-contrôle juste avant de tirer : entre la phase 1 et ici il y a eu un
            # dump SRAM, parfois un armement et un reset. On vérifie que la puce est
            # TOUJOURS dans l'état qu'on croit, plutôt que d'attaquer à l'aveugle.
            log("  Contrôle du niveau juste avant la passe :")
            level_now = probe_level(ser, log)
            if level_now != level:
                log(f"  ✗ Le niveau a CHANGÉ depuis la phase 1 : {level} → {level_now}.")
                log("    Passe annulée — un résultat obtenu dans un état non maîtrisé ne")
                log("    vaut rien. Reprendre depuis le début.")
                return 1
            if level_now != "L1" and not args.allow_level0:
                log(f"  ✗ La puce est au {level_now}, pas au Level 1 : passe annulée.")
                return 1
            log(f"  ✓ toujours {level_now}")

            send(ser, "SWD HALT", settle=0.4, timeout=20)
            t0 = time.time()
            core, code, verdict = read_via_core(ser, args.probe_addr,
                                                args.probe_words * 4, log)
            if core is None:
                log("")
                log(f"╔═ VERDICT : ÉCHEC RAMREAD [{code}] " + "═" * 30)
                log("  " + verdict)
                log("╚" + "═" * 60)
                return 1
            log(f"  ✓ {len(core)} octets en {time.time() - t0:.0f}s, "
                f"md5 {hashlib.md5(core).hexdigest()}")
            # Écrit AVANT la comparaison : un tampon qui diverge de la référence reste
            # la seule capture existante de ce que le cœur a lu sous Level 1.
            probe_path = (f"{args.out_dir}/bat32_probe_{level.lower()}_"
                          f"0x{args.probe_addr:08X}_{stamp}.bin")
            open(probe_path, "wb").write(core)
            log(f"    → {probe_path}")

            # ── Phase 6 — le tampon dit-il la vérité ? ────────────────────────
            log.phase(6, "vérification du contenu")
            if mode == "validation":
                dbg = read_via_debugger(ser, args.probe_addr, args.probe_words * 4,
                                        log, "debugger")
                if dbg is None:
                    log("  ✗ SWD READ a fauté alors que le niveau mesuré est L0 — incohérent.")
                    return 1
                other, label = dbg, "SWD READ"
            else:
                lo = args.probe_addr - CODE_FLASH
                other = reference[lo:lo + len(core)]
                label = args.ref or "référence relue avant armement"
                if len(other) < len(core):
                    log(f"  ✗ La référence ne couvre pas 0x{args.probe_addr:08X}+{len(core)}.")
                    return 1

            if core == other:
                log(f"  ✓ IDENTIQUE à {label}")
                if mode == "validation":
                    log("")
                    log("╔═ VERDICT : HARNAIS VALIDÉ " + "═" * 34)
                    log("  Le mécanisme RAMREAD marche et ce script le lit correctement.")
                    log("  Ça ne dit RIEN du Level 1 — c'était le but. Étape suivante :")
                    log("    1. dump frais :  python3 scripts/bat32_dump.py --out-dir <dir>")
                    log("    2. relancer ce script avec --arm --backup-code … --backup-data …")
                    log("╚" + "═" * 60)
                else:
                    log("")
                    log("╔═ VERDICT : ★★★ LEVEL 1 CONTOURNÉ " + "═" * 27)
                    log("  Le cœur a lu la flash que le debugger ne peut plus lire, et le")
                    log("  contenu correspond à la référence. La protection ne vise que le")
                    log("  debugger — la question ouverte n°6 est tranchée, POSITIVEMENT.")
                    log("  ⇒ Les phases de glitch (07 §6/§9ter.5, 08 §3-§5) deviennent")
                    log("    inutiles pour le Level 1. Passer au dump complet : --full.")
                    log("╚" + "═" * 60)
            else:
                log(f"  ✗ DIFFÉRENT de {label}")
                diff_report(core, other, args.probe_addr, log)
                log("")
                log("╔═ VERDICT : RAMREAD A RENDU DES DONNÉES, MAIS FAUSSES " + "═" * 8)
                log("  Le mécanisme a tourné (le cœur a atteint son BKPT) mais le contenu ne")
                log("  correspond pas. Trois causes, dans l'ordre de vraisemblance :")
                log("    1. la référence n'est pas celle de CETTE puce — trois md5 de code")
                log("       flash coexistent dans assets/ (6f37bd86 / 2d03db71 / 2c92a6a2) ;")
                log("       le dernier état connu du banc est 6f37bd86 (2026-09-02) ;")
                log("    2. le motif se répète à partir de l'offset 0x3E0 ⇒ firmware < v0.13")
                log("       (TAR non ré-armé à la frontière de 1 Ko), pas un effet du Level 1 ;")
                log("    3. le tampon SRAM est périmé et le cœur n'a rien copié — mais le")
                log("       firmware refuse ce cas (contrôle du PC), donc peu probable.")
                log("╚" + "═" * 60)
                return 1

            # ── Phase 7 — dump complet ────────────────────────────────────────
            if args.full:
                log.phase(7, "dump complet des 64 Ko par le cœur")
                log("  ⚠ Le dump complet vise TOUJOURS la code flash 0x0, quel que soit\n"
                    f"    --probe-addr (ici 0x{args.probe_addr:08X}) : si la passe d'essai a\n"
                    "    porté ailleurs — une adresse SRAM pour départager un CORE_FAULT —\n"
                    "    elle n'a PAS éprouvé ce que cette phase va lire.")
                t0 = time.time()
                full, code, verdict = read_via_core(ser, CODE_FLASH, CODE_SIZE, log)
                if full is None:
                    log(f"  ✗ échec [{code}] : {verdict}")
                    return 1
                path = f"{args.out_dir}/bat32_code_flash_ramread_{level.lower()}_{stamp}.bin"
                open(path, "wb").write(full)
                log(f"  ✓ {len(full)} octets en {time.time() - t0:.0f}s → {path}")
                log(f"    md5 {hashlib.md5(full).hexdigest()}")
                if reference and len(reference) >= CODE_SIZE:
                    if full == reference[:CODE_SIZE]:
                        log("  ✓ 64 Ko IDENTIQUES à la référence")
                    else:
                        log("  ✗ divergence sur les 64 Ko :")
                        diff_report(full, reference[:CODE_SIZE], CODE_FLASH, log)
                        return 1

        finally:
            if core_touched:
                log("")
                log("── Remise en marche de la cible " + "─" * 32)
                try:
                    send(ser, "TARGET RESET", settle=0.6, timeout=30)
                    log("  ✓ TARGET RESET — cœur relancé.")
                    log("  ⚠ Le boot reconstruit l'ESSENTIEL du `.data` écrasé, pas tout :")
                    log("    mesuré sur le banc le 2026-09-06, 93 % de la zone")
                    log(f"    0x{PAYLOAD_LO:08X}-0x{PAYLOAD_HI:08X} revient à sa valeur d'avant ;")
                    log("    ~282 octets gardent ce que le payload y a laissé. L'écrasement")
                    log("    est donc PARTIELLEMENT IRRÉVERSIBLE — le dump de la phase 2 est")
                    log("    la seule copie fidèle de la SRAM d'origine.")
                except Exception as exc:
                    log(f"  ⚠ TARGET RESET impossible ({exc}). La cible reste HALTÉE et sa\n"
                        "    SRAM contient le payload : la reseter à la main avant tout usage.")
    log("")
    log(f"Journal : {log.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
