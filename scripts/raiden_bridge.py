#!/usr/bin/env python3
"""Pont entre la CLI Raiden et le reseau, dans les deux sens.

Deux roles, selon le cote ou l'on se trouve :

    serve  -- ecoute en TCP et relaie vers le port serie local.
              Sert a exercer TOUT le chemin hote (scripts, timeouts, debit)
              **avant** que le firmware ne parle TCP, contre le firmware
              actuel et son USB CDC. C'est l'etape 1 du plan.

    pty    -- se connecte au firmware en TCP et expose un pseudo-terminal
              local. Les scripts pyserial fonctionnent alors **sans aucune
              modification** et a pleine vitesse.

Le mode `pty` est le chemin recommande a l'usage, et la raison est mesurable :
sur une URL `socket://`, `in_waiting` de pyserial renvoie `len(select(...))`,
c'est-a-dire **0 ou 1, jamais un nombre d'octets**
(`serial/urlhandler/protocol_socket.py:136-142`). Tout script qui fait
`read(in_waiting)` dans une boucle temporisee retombe alors a un octet par
tour. Sur un pty, `in_waiting` passe par `TIOCINQ` et rend un vrai compte.

Ce script remplace `socat` (pas de dependance systeme, pas de privileges).

Exemples :
    # Cote banc, avant d'avoir le firmware TCP :
    python3 scripts/raiden_bridge.py serve --serial /dev/ttyACM0
    python3 scripts/bat32_dump.py --port socket://localhost:5000

    # Cote poste de travail, une fois le firmware TCP en place :
    python3 scripts/raiden_bridge.py pty --target 192.168.1.42:5000
    python3 scripts/bat32_dump.py --port /tmp/raiden

Prerequis : pyserial (mode `serve` uniquement).
"""
import argparse
import os
import pty
import select
import signal
import socket
import sys
import termios
import tty

BUF = 4096


def _relay(a, b, label_a="", label_b="", verbose=False):
    """Relaie les octets entre deux descripteurs jusqu'a fermeture."""
    fds = [a, b]
    total = {a: 0, b: 0}
    try:
        while True:
            ready, _, bad = select.select(fds, [], fds, 1.0)
            if bad:
                return
            for fd in ready:
                try:
                    data = os.read(fd, BUF)
                except OSError:
                    return
                if not data:
                    return
                other = b if fd is a else a
                total[fd] += len(data)
                try:
                    os.write(other, data)
                except OSError:
                    return
    finally:
        if verbose:
            print(f"\n  {label_a} -> {label_b} : {total[a]} octets"
                  f"\n  {label_b} -> {label_a} : {total[b]} octets", file=sys.stderr)


def cmd_serve(args):
    """Ecoute en TCP, relaie vers le port serie. Un client a la fois."""
    try:
        import serial
    except ImportError:
        sys.exit("pyserial manquant : pip install pyserial")

    host, _, port = args.listen.rpartition(":")
    host = host or "0.0.0.0"
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, int(port)))
    srv.listen(1)
    print(f"[bridge] ecoute sur {host}:{port} -> {args.serial}", file=sys.stderr)

    while True:
        conn, peer = srv.accept()
        print(f"[bridge] client {peer[0]}:{peer[1]}", file=sys.stderr)
        # Un client a la fois : la CLI a un etat (api_mode, armement, cible),
        # deux sessions concurrentes se le corrompraient mutuellement.
        try:
            ser = serial.Serial(args.serial, args.baud, timeout=0)
        except Exception as e:                                  # noqa: BLE001
            print(f"[bridge] port serie indisponible : {e}", file=sys.stderr)
            conn.close()
            continue
        conn.setblocking(False)
        try:
            _relay(conn.fileno(), ser.fileno(), "tcp", "serie", args.verbose)
        finally:
            conn.close()
            ser.close()
            print("[bridge] client parti", file=sys.stderr)
        if args.once:
            return


def cmd_pty(args):
    """Se connecte au firmware en TCP et expose un pty local."""
    host, _, port = args.target.rpartition(":")
    sock = socket.create_connection((host, int(port)), timeout=10)
    sock.setblocking(False)

    master, slave = pty.openpty()
    tty.setraw(slave, termios.TCSANOW)
    name = os.ttyname(slave)

    link = args.link
    if link:
        try:
            if os.path.islink(link) or os.path.exists(link):
                os.unlink(link)
            os.symlink(name, link)
        except OSError as e:
            print(f"[bridge] lien {link} impossible ({e}), utiliser {name}",
                  file=sys.stderr)
            link = None

    print(f"[bridge] {args.target} <-> {link or name}", file=sys.stderr)
    print(f"[bridge] exemple : python3 scripts/bat32_dump.py --port {link or name}",
          file=sys.stderr)

    def _cleanup(*_):
        if link and os.path.islink(link):
            try:
                os.unlink(link)
            except OSError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)
    try:
        _relay(sock.fileno(), master, "tcp", "pty", args.verbose)
    finally:
        _cleanup()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="TCP -> port serie (test du chemin hote)")
    s.add_argument("--serial", default="/dev/ttyACM0")
    s.add_argument("--baud", type=int, default=115200)
    s.add_argument("--listen", default="0.0.0.0:5000")
    s.add_argument("--once", action="store_true",
                   help="quitter apres le premier client")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(func=cmd_serve)

    p = sub.add_parser("pty", help="TCP -> pseudo-terminal local")
    p.add_argument("--target", required=True, help="hote:port du firmware")
    p.add_argument("--link", default="/tmp/raiden",
                   help="lien symbolique vers le pty (defaut /tmp/raiden)")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_pty)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
