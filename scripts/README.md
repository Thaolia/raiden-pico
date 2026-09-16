# Raiden Pico Glitch Testing Scripts

This directory contains Python scripts for automated glitch testing and analysis.

## Requirements

```bash
pip install pyserial
```

## BAT32G135 (Cmsemicon) — SWD

Six scripts, dans l'ordre où on s'en sert. Tous supposent `TARGET BAT32` et
une horloge SWD lente (`--speed 4`, ~125 kHz) : à `SPEED 0` cette cible imite
parfaitement une puce verrouillée. Un `TARGET RESET` est pulsé avant chaque
`SWD CONNECT` (`--no-reset` pour s'en passer).

> ★★ **Le chip erase n'efface que la code flash.** Mesuré le 2026-09-02 : après
> un `SWD BAT32 CHIPERASE CONFIRM` réussi, la data flash était intacte, appairage
> compris. Pour la blanchir il faut `SWD BAT32 SECTORERASE <addr> CONFIRM`
> (secteur = **512 octets**, donc trois pour les 1,5 Ko : `0x500000`, `0x500200`,
> `0x500400`). Autrement dit : **sortir du Level 1 par chip erase ne coûte pas
> l'appairage.**

> ★ **Halter le cœur avant toute lecture flash.** Cœur en marche, une lecture
> SWD ramène par moments ce que le cœur est en train de préfetcher au lieu de
> l'adresse demandée : `0x00004910` rendait `70 47 C0 46` (`bx lr; nop`) cœur
> actif et `FF FF FF FF` juste après un `SWD HALT`, de façon reproductible. Les
> scripts de lecture halten maintenant d'eux-mêmes ; le seul cas où l'on s'en passe
> (`bat32_dump.py --no-halt`) est l'observation délibérée d'une cible qui
> tourne. **Devant une divergence inattendue, halter et relire avant de
> conclure quoi que ce soit sur le contenu de la flash.**

### bat32_dump.py

Dump complet (code flash, data flash, SRAM) par le chemin debugger, avec
relecture et arbitrage des octets litigieux. C'est le dump de référence.

```bash
python3 scripts/bat32_dump.py --out-dir dumps/
```

### bat32_ramread_compare.py

Vérifie `SWD BAT32 RAMREAD`, la lecture de la flash **par le cœur** (le bypass
du Level 1). `--mode compare` (défaut) exige que RAMREAD et `SWD READ` rendent
la même chose — c'est le test du mécanisme, au Level 0. `--mode ref` compare à
une image de référence, pour le Level 1 où `SWD READ` faute.

```bash
python3 scripts/bat32_ramread_compare.py
python3 scripts/bat32_ramread_compare.py --mode ref \
    --ref assets/bat32_dump_20260831/bat32_code_flash.bin --out /tmp/l1.bin
```

### bat32_l1_ramread_test.py

L'**expérience** que `bat32_ramread_compare.py` ne fait pas : conduire le test
du bypass au Level 1 de bout en bout, dans le bon ordre, et **classer l'échec**
quand il échoue. C'est la priorité −1 du protocole (08 §9) et la question
ouverte n°6.

★ **Le Level 1 est exigé, et vérifié deux fois** — en phase 1, puis à nouveau
juste avant la passe d'essai (entre les deux il y a eu un dump SRAM, parfois un
armement et un reset). Ce n'est pas de la prudence, c'est de la logique : au
Level 0 le debugger a déjà le droit de lire la flash, donc **un RAMREAD réussi
n'y prouve rien du bypass**. Le niveau est établi par ce que la puce *fait* — la
flash faute, la SRAM répond — jamais par `SWD OPT`, dont les option bytes vivent
en code flash et sont donc illisibles précisément au Level 1.

Puce au Level 1 → test du bypass, `--ref` obligatoire. `--arm` → armement puis
test. `--allow-level0` → la seule façon d'attaquer une puce non protégée, et
elle est explicite : elle sert à éprouver le harnais. Sans rien de tout ça, une
puce au Level 0 fait **refuser** le script, avec les trois suites possibles.

Ce qu'il prend en charge et qu'on oublie à la main : **le dump SRAM avant la
première passe** (RAMREAD écrase `0x20000000`-`0x2000101F`, où vit le `.data`
recopié de la flash au boot — la fuite gratuite du Level 1), et surtout le
**classement des neuf messages `[BAT32-RAM]`** du firmware en hypothèses
distinctes. Le cas décisif est `CORE_FAULT` (DFSR bit 3, VCATCH) : le copieur a
fauté en lisant la flash, donc la protection gêne **aussi le cœur** et la voie
07 §2bis.5 est close. À ne pas confondre avec `PC_ELSEWHERE` (bit 1), qui ne
conclut rien.

La cible est **remise en marche en sortant** (`TARGET RESET`) sur tous les
chemins, y compris les échecs : la phase 2 halte le cœur et RAMREAD lui écrase
4 Ko de SRAM, ni l'un ni l'autre ne doit lui survivre.

> ⚠ **Mais le reset ne rend pas toute la SRAM.** Mesuré sur le banc le
> 2026-09-06 : **93 %** de la zone `0x20000000`-`0x2000101F` revient à sa valeur
> d'avant, ~282 octets gardent ce que le payload y a laissé — le copieur lui-même
> n'était réécrit qu'à moitié (deux premiers mots à zéro, deux suivants intacts).
> Le boot n'initialise que ce que `.data`/`.bss` couvrent. **L'écrasement est
> partiellement irréversible**, donc le dump SRAM de la phase 2 est la seule
> copie fidèle et il n'y a pas de seconde chance de le prendre. La flash, elle,
> n'est jamais touchée hors `--arm`.

```bash
# 1. Éprouver le harnais au Level 0 — à faire en premier
#    (halte le cœur, écrase 4 Ko de SRAM cible, reset en sortie ; flash intacte)
python3 scripts/bat32_l1_ramread_test.py --allow-level0

# 2. Séquence complète : relit la puce, compare aux sauvegardes, arme, teste
python3 scripts/bat32_dump.py --out-dir /tmp/backup_avant_l1
python3 scripts/bat32_l1_ramread_test.py --arm \
    --backup-code /tmp/backup_avant_l1/bat32_code_flash.bin \
    --backup-data /tmp/backup_avant_l1/bat32_data_flash.bin

# 3. Puce déjà au Level 1
python3 scripts/bat32_l1_ramread_test.py --full \
    --ref assets/bat32_dump_20260831/bat32_code_flash.bin
```

`--arm` est le seul chemin destructif, et il traite les deux flashs
différemment — parce qu'elles ne se comportent pas pareil :

- **code flash : égalité stricte exigée.** Elle ne dérive pas d'elle-même, donc
  un écart signifie que la sauvegarde n'est pas celle de cette puce. Et c'est
  elle que le chip erase détruira.
- **data flash : dérive tolérée** jusqu'à `--data-drift-max` (défaut 64 octets),
  rapportée zone par zone. Elle est **vivante** : 12 octets ont bougé en dix
  minutes le 2026-09-02. Exiger l'égalité ferait échouer l'armement pour la
  raison la plus normale du monde — une sauvegarde prise cinq minutes plus tôt.

Avant toute comparaison, il **écrit les deux relectures** sous
`bat32_{code,data}_flash_avant_arm_<horodatage>.bin` : c'est l'état exact de la
puce à l'instant de l'armement, et c'est lui qui fait foi, pas le fichier
fourni. Puis il demande une confirmation littérale. Rappel de ce qu'il engage :
la seule sortie du Level 1 est le chip erase, qui détruit la code flash.

### bat32_restore.py

Restaure la code flash depuis une image. Ne réécrit que les blocs qui
diffèrent, refuse d'avance tout bloc exigeant un bit 0→1 (la programmation ne
fait que 1→0 — il faut alors un erase), et ne conclut qu'après **relecture**.
La relecture de contrôle se fait **après un `TARGET RESET`** : enchaînée sur la
programmation, elle peut être servie par le buffer du contrôleur flash et
confirmer une valeur que la cellule ne porte pas. `--confirm` est obligatoire
pour écrire ; sans lui, diff seulement.

```bash
python3 scripts/bat32_restore.py --image assets/bat32_dump_20260831/bat32_code_flash.bin
python3 scripts/bat32_restore.py --image ... --confirm
```

### bat32_dataflash.py

Sauvegarde et restaure la **data flash** (`0x00500000`, 1,5 Ko) — c'est là que
vit l'appairage du capteur, et le chip erase qui fait sortir du Level 1
l'emporte. À lancer **avant** toute manip qui peut effacer.

```bash
python3 scripts/bat32_dataflash.py backup                       # instantané horodaté
python3 scripts/bat32_dataflash.py restore --image <f> --confirm
```

Trois choses à savoir :

- **La data flash est vivante** : 12 octets ont changé en dix minutes de
  fonctionnement (compteurs + un journal à `0x00500453`). Une sauvegarde est un
  instantané ; la prendre juste avant l'opération.
- Certains incréments remontent des bits à 1, donc le capteur **efface lui-même
  des pages**. Restaurer une image antérieure sur une puce qui a tourné se
  heurte au refus « bits 0→1 » — c'est normal, le restore vise une puce
  fraîchement effacée. Et un chip erase **ne suffit pas** à la blanchir : il faut
  `SWD BAT32 SECTORERASE` sur `0x500000`, `0x500200` et `0x500400`.
- ✅ Le chemin d'écriture est **validé** depuis le 2026-09-02 : image du 01/09
  reposée sur une data flash blanchie, relue après reset à `4a5114b8…`.
- ⚠ `0x00500004` porte **OCDM** : `0x3C` y ferme la puce en **Level 2**, sans
  retour. Ce bloc est **exclu par défaut** (`--include-ocdm` pour le forcer) et
  écrire `0x3C` y est refusé sans échappatoire.

Le chemin d'écriture n'ayant jamais été exercé sur la data flash, le restore
commence par **un bloc témoin** vérifié après reset, et n'écrit la suite que si
ce témoin a pris.

### raiden_bridge.py

Pont entre la CLI et le réseau, sans dépendance système (remplace `socat`).

```bash
# Exercer le chemin hôte AVANT que le firmware ne parle TCP :
python3 scripts/raiden_bridge.py serve --serial /dev/ttyACM0
python3 scripts/bat32_dump.py --port socket://localhost:5000

# Une fois le firmware TCP en place, exposer la session comme un port série :
python3 scripts/raiden_bridge.py pty --target 192.168.1.42:5000
python3 scripts/bat32_dump.py --port /tmp/raiden
```

★ **Préférer le mode `pty`.** Sur une URL `socket://`, `in_waiting` de pyserial
vaut 0 ou 1 et jamais un nombre d'octets : tout script qui fait
`read(in_waiting)` retombe à ~49 o/s **avec troncature silencieuse** (mesuré).
Sur un pty, `in_waiting` passe par `TIOCINQ` et rend un vrai compte — 201 ko/s,
scripts non modifiés. Les scripts `bat32_*` d'ici sont corrigés et fonctionnent
sur les deux chemins, mais tout script tiers ne l'est pas.

### bat32_race_sweep.py

Balaye le délai de la course au relâchement du reset (`SWD RACE`). Conclusion
mesurée : la fenêtre A est imprenable sur cette puce — avant 450 µs le système
entier est en reset et ne rend que des zéros, SRAM comprise.

## Scripts

### test_chipshouter_lpc_glitch.py

Tests ChipSHOUTER glitching of the LPC bootloader by attempting to bypass security checks during the bootloader read command.

**Usage:**

```bash
# Single glitch test with default parameters (V=350, Pause=8000, Width=150 cycles)
./scripts/test_chipshouter_lpc_glitch.py

# Run multiple iterations
./scripts/test_chipshouter_lpc_glitch.py -n 10

# Custom voltage, pause, and pulse width
./scripts/test_chipshouter_lpc_glitch.py -v 320 --pause 10000 -p 200

# Custom voltage reduction step (default: 10V)
./scripts/test_chipshouter_lpc_glitch.py --voltage-step 20

# Quiet mode (less verbose output)
./scripts/test_chipshouter_lpc_glitch.py -q

# Multiple iterations with custom parameters
./scripts/test_chipshouter_lpc_glitch.py -n 100 -v 350 --pause 8000 -p 150
```

**Parameters:**

- `-n, --num-iterations`: Number of test iterations (default: 1)
- `-v, --voltage`: ChipSHOUTER voltage in volts (default: 350)
- `--pause`: Pico glitch pause in cycles (default: 8000)
- `-p, --pulse-width`: Pico glitch pulse width in cycles (default: 150)
- `--voltage-step`: Voltage reduction step on no-response failures (default: 10)
- `-q, --quiet`: Suppress verbose output

**Test Sequence:**

1. Reboots the Pico for clean state
2. Resets ChipSHOUTER
3. Sets target to LPC
4. Syncs with LPC bootloader (115200 baud, 12MHz crystal, 10ms reset delay)
5. Configures glitch parameters:
   - ChipSHOUTER voltage
   - Pico glitch pause (delay before glitch trigger)
   - Pico glitch pulse width
6. Configures UART trigger on byte 0x0d (carriage return)
7. Sets ChipSHOUTER to hardware trigger HIGH
8. Arms ChipSHOUTER
9. Arms Pico trigger system
10. Sends "R 0 516096" read command to target
11. Analyzes response:
    - Response "19" = Glitch FAILED (normal error response)
    - Response "0" = Glitch SUCCESS (security bypass)
    - No response = Target hung/crashed (retries with reduced voltage)
12. If no response: Automatically reduces ChipSHOUTER voltage by voltage_step and retries until getting error 19 or success

**Example Output:**

```
ChipSHOUTER LPC Bootloader Glitch Test
============================================================

Iteration 1/10
------------------------------------------------------------
Rebooting Pico...
Reconnected to Pico

Setting up LPC target...
LPC bootloader sync complete

Configuring glitch parameters...

Performing glitch test...

✓ GLITCH SUCCESS - Security bypass detected!
```

### glitch_marathon.py

Long-running parameter sweep designed to run for hours. Tests parameter space repeatedly to find rare glitch windows. Logs all results to timestamped CSV files.

**Usage:**

```bash
# Run for 8 hours (default: 12 hours)
./scripts/glitch_marathon.py --hours 8

# Run in background
nohup python3 scripts/glitch_marathon.py --hours 24 > marathon.log 2>&1 &

# Monitor progress
tail -f marathon.log

# Analyze results
python3 scripts/analyze_glitch_results.py glitch_results_*.csv
```

**Parameter Space:**
- Voltage: 300-500V (5 values)
- Pause: 0-8000 cycles (fine granularity near response timing)
- Width: 100-250 cycles (7 values)
- Total combinations: ~1540 per cycle

**Performance:**
- ~0.43 tests/second (2.3s per test)
- ~1500 tests/hour
- Logs all results to `glitch_results_YYYYMMDD_HHMMSS.csv`

### analyze_glitch_results.py

Analyzes glitch test results from CSV log files.

**Usage:**

```bash
# Analyze specific file
python3 scripts/analyze_glitch_results.py glitch_results_20251028_081812.csv

# Analyze latest results
python3 scripts/analyze_glitch_results.py glitch_results_*.csv
```

**Output:**
- Success rate statistics
- Successful parameter combinations
- Crash parameter ranges
- Timing statistics

## Notes

- All scripts use `/dev/ttyACM0` for Pico serial communication
- Default baud rate: 115200
- Scripts automatically handle Pico reboot and reconnection
- For best results, ensure ChipSHOUTER is properly configured with appropriate voltage, pulse width, and positioning
- **Marathon tests**: Run for hours (8-24h) to find narrow glitch windows
- **Results**: All results logged to timestamped CSV files for later analysis
