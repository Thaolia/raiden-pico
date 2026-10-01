# Changelog

All notable changes to the Raiden Pico firmware. The version is the string the
`VERSION` CLI command prints (defined in `src/command_parser.c`); bump it in the
same change (see the `version-bump` skill) and add an entry here.

Format loosely follows [Keep a Changelog](https://keepachangelog.com/). This file
was started at v0.7, so pre-0.6 entries are summarized from git history.

> **Numérotation de ce fork.** Cette branche part de `main` amont. Depuis la
> fusion du 2026-10-01, l'amont a réellement publié les versions **0.8 à 0.13**
> (section « Amont » ci-dessous, reprise telle quelle de l'amont). Le fork avait
> par ailleurs développé son propre travail sous les numéros **0.8 à 0.14** AVANT
> cette fusion : ces sections (« Fork JLQ » plus bas) sont conservées telles
> quelles — ce sont les notes de banc du fork, et leurs numéros ne correspondent
> pas à ceux de l'amont. Ce que `VERSION` imprime est désormais
> `v0.13-JLQ_AA/MM/JJ-vN` — base amont v0.13, auteur du fork, date, itération.

## Amont (upstream) — versions publiées

## [0.13] — 2026-09-29 — SWD LEAKPROBE + flash-leak experiment (negative)

### Added
- **`SWD LEAKPROBE <addr>`** — atomic flash-read-leak probe: does a MEM-AP read of
  a (possibly RDP-blocked) address and captures the raw DRW data phase, RDBUFF, and
  sticky-error state with **no intervening error-clear**, so the per-command
  auto-clear can't wipe transient residue. Baselines with a known SRAM read to
  distinguish stale pipeline data from a real leak.

### Findings
- **Flash-read-leak experiment: NO LEAK (clean block).** Programmed an
  address-encoding pattern to flash at RDP0, re-locked to RDP1, and probed the CPU
  `ldr` path (HardFault, no data) and the debug MEM-AP path (`LEAKPROBE`: stale
  baseline + STICKYERR, no flash data). Flash data never leaves the flash-controller
  boundary. Recorded in `RDP1_DEBUG_MATRIX.md`.

## [0.12] — 2026-09-29 — SWD SCAN (DAP / CoreSight enumeration)

Feature branch (version tag distinguishes it from the parallel I2C branch; the
final number is assigned at merge).

### Added
- **`SWD SCAN`** — enumerates the ADIv5 DAP over SWD: Access Ports (reads each
  `AP_IDR`) and, for each MEM-AP, walks the CoreSight ROM table (`BASE` → entries
  → per-component `PIDR`/`CIDR`), naming components by their ARMv7-M debug base
  (SCS, DWT, FPB/BPU, ITM, TPIU, ETM). SWD-only — no JTAG needed. Powers up the
  debug/system domains first so AP reads work before any mem access.
  Bench-verified on STM32F401 at **both RDP0 and RDP1**: the debug topology
  enumerates identically when locked (PPB/ROM-table stays readable) even though
  application flash/SRAM MEM reads fault (ACK=0x4). Registered in the SWD matcher.

## [0.11] — 2026-09-28 — Explicit glitch-voltage arg + robust auto-detect

### Added
- **`[voltage_mv]` argument on `TARGET GLITCH BYPASS` and `SHADOWBYPASS`.** Establish
  the glitch depth once with `TARGET GLITCH SWEEP`, then pass it (millivolts, 0–3300)
  to skip re-sweeping every run. On BYPASS it sets the calibrated threshold directly
  and skips the sweep; on SHADOWBYPASS it ADC-gates the recovery dip to that depth
  instead of the legacy uncontrolled fixed-time low pull. Invalid/out-of-range values
  error at parse time (config_none regression tests added).

### Changed
- **`ensure_target_type()` now auto-detects the robust way `SWD IDCODE` does** —
  `target_power_ensure_on()` + `swd_ensure_connected()` + `swd_clear_errors()` before
  reading DEV_ID. The old bare `swd_connect()` path faulted (ACK=0x7) when a preceding
  glitch left the target mid-boot, so `SWEEP`/`BYPASS`/`SHADOWBYPASS` could no longer
  self-set the target. Now they recover and detect on their own.

## [0.10] — 2026-09-28 — STM32F4 RDP1 BYPASS + F4 flash-controller support

### Added
- **STM32F4 RDP1 BYPASS payload.** `TARGET GLITCH BYPASS` is now per-family via a
  `get_rdp_bypass_payload()` selector: F1 unchanged, **F4 added** (F4 peripheral
  map — RCC 0x40023800, GPIOA AHB1, USART1 PA9/AF7 @115200 — plus the FPB reader
  trick F4 needs to read flash from SRAM-executing code under RDP1). F2/F3 return
  an explicit error. Payload `stm32_payloads/f4/rdp_bypass.S`.
- **`TARGET GLITCH SHADOWCHAR [n]`** — M1 of the deterministic POR option-byte
  shadow-load glitch plan. Power-cycles the target and timestamps `t_vdd` (VDD
  rising through ~2.0 V on ADC GP26) and `t_nrst` (nRST/GP15 release after POR)
  over N iterations, reporting the `[t_vdd, t_nrst]` window (where the option-byte
  shadow load happens) plus nRST jitter — i.e. how lockable a timed glitch delay
  can be. Non-destructive; runs at RDP0 or RDP1.
- `rdp-payload-check` skill (prove an RDP flash payload reads real flash at RDP0
  before trusting any RDP1 result) and `tty-contention-check` skill (`fuser` the
  serial port before use — empty CLI reads usually mean a second terminal, not a
  dead device).

### Fixed
- **F4 flash-controller support** — SWD flash erase/write/fill worked on F1 but
  failed on F4. Fixed in `swd.c`: `flash_wait` reads BSY at **bit 16** (F4/L4), not
  bit 0; F4 erase/program clear the sticky SR error/EOP flags first; `flash_unlock`
  is **idempotent** (on F4, re-writing KEYR while already unlocked re-locks it — the
  double-unlock that broke erase+write). `SWD FILL` flash chunk capped at the 2 KB
  buffer (F4's 16 KB sector `page_size` over-read it). All 16 SWD subcommands now
  verified on an F401 (SRAM full 96 KB, flash, system memory, peripherals, regs).

## [0.9.1] — 2026-09-27 — RDP1 deep-sleep control test (research/diagnostic)

### Added
- **`TARGET GLITCH CLEANWAKE`** — a glitch-free control test for the STM32F1 RDP1
  investigation. Uploads an SRAM payload, resumes with `C_DEBUGEN=1`, detaches
  SWD, and lets the payload run autonomously: it announces itself (`CLN0`), enters
  STOP, self-wakes via RTC (~2 ms, `WAKE`), reports its own `DHCSR` + `FLASH_OBR`,
  then attempts a direct flash read. Payload: `stm32_payloads/f1/rdp_cleanwake.S`.

### Changed
- `TARGET GLITCH HALT` diag payload (`stm32_payloads/f1/rdp_bypass_diag.S`) now
  reads flash with a direct `ldr` instead of the F2/F4-only FPB "reader" trick
  (unneeded and fault-prone on F1).

### Findings (bench, F1 at RDP1; BYPASS as proven control)
- Deep sleep genuinely disconnects debug on F1: resumed with `C_DEBUGEN=1`, after
  the autonomous STOP/wake the payload read back `C_DEBUGEN=0` (it never writes
  DHCSR). **But this does not bypass RDP1** — `FLASH_OBR RDPRT=1` and the read
  faults. RDP1 is the flash-controller POR latch, independent of debug state; only
  the voltage glitch corrupts it. On F1, SRAM code reads flash directly once RDP is
  down — the FPB reader trick is an F2/F4 requirement, not F1.

## [0.9] — 2026-09-27 — External PSU control (TENMA / Multicomp Pro 72-2540)

### Added
- **External programmable PSU control.** New `PSU` command family
  (`VOLT/CURR/ON/OFF/STATUS/ID/RELEASE`) driving a TENMA 72-2540 / Korad-protocol
  supply over UART1 routed to GP10/11 (9600 8N1) via the RP2350 alternate
  funcsel. `src/psu.c` + `include/psu.h`. Needs a MAX3232 on the PSU's RS-232 DB9.
- Mutually exclusive with the GP10/11/12 target power group: a PSU command
  releases the power group and claims the pins; `TARGET POWER` refuses while the
  PSU holds them (run `PSU RELEASE`). New `power_group_release()` in target_uart.c.
- **Verified end-to-end on real hardware** through the Pico: Pico GP10/11 →
  YL-97 (MAX3232) → RS-232 DB9 → 72-2540. `PSU ID` returns the unit's identity
  (tested on `Multicomp Pro 72-2540 V6.1` and `TENMA 72-2540 V5.9`), `PSU VOLT`/
  `CURR` set and read back, `PSU ON`/`OFF` drive the output with the correct
  STATUS decode (bit0 CV/CC, bit4 beep, bit5 lock, bit6 output), `PSU STATUS`
  reports live Vout/Iout, and the `TARGET POWER` mutual-exclusion guard fires.
  Protocol: 9600 8N1, no terminator; `VSET1:NN.NN`/`ISET1:N.NNN` write formats.
  Wiring note: the RS-232 DB9 needs pin-5 GND common to the converter, and the
  converter↔PSU link must be wired for the DB9 orientation (both are DCE).
  config_none tests cover the CLI error/parse paths.

(Version 0.10 is the separate STM32F4 BYPASS branch, still bench-pending; the two branches
reconcile at merge time — this PSU work lands first as 0.9.)

## [0.8] — 2026-09-19 — Pico 2 W support + Target/GRBL UART bleed fix

### Added
- **Pico 2 W support.** The status LED is now board-aware via the SDK's
  `PICO_DEFAULT_LED_PIN` (GP25 on Pico 2). On the Pico 2 W the LED is on the
  CYW43 chip and GP25 is its chip-select (WL_CS), so the firmware no-ops the LED
  instead of driving GP25 — no wireless stack pulled in. New `BOARD=pico2_w`
  build option; `PINS` output is board-aware.

### Fixed
- **Target↔GRBL UART1 "TTL bleed."** `target_initialized` latched true and was
  never cleared when GRBL took UART1 (GP8/9), so a `TARGET SEND` / bootloader /
  ISP command after any GRBL command wrote to UART1 while it was still on GP8/9,
  bleeding bootloader traffic onto the GRBL controller. Target TX now
  auto-reclaims UART1 to GP4/5 (via `target_uart_ensure_active()`, applied to
  the send and STM32/LPC ISP-entry paths) and prints
  `OK: UART1 reclaimed from GRBL for Target (GP4/5)`. The manual `TARGET SYNC`
  after GRBL is no longer required. Verified electrically with dual FTDI probes.

## Fork JLQ — journal de banc local (numéros 0.8–0.14, antérieurs à la fusion)

## [0.7-JLQ_26/09/30-v7] — `SWD DISCONNECT` rejeté par le portillon de validation

### Fixed
- **`SWD DISCONNECT` fonctionne à nouveau.** La sous-commande était gérée par l'exécuteur
  (`swd_deinit()` → SWCLK/SWDIO en haute impédance) et annoncée par le HELP, mais **absente de la
  liste blanche `swd_subcmds[]`** du portillon d'abréviation : toute saisie rendait
  `ERROR: Unknown SWD sub-command 'DISCONNECT'` avant même d'atteindre le handler. Ajoutée à la
  liste (count 21 → 22, aux côtés de `GLITCH`). Sans elle, impossible de relâcher les lignes SWD en
  haute-Z avant une coupure d'alimentation — le debugger tient alors SWDIO à 3,3 V et réalimente la
  cible par ses diodes de protection (mesuré au banc : 0,74 V sur VDD alors que le relais est OFF).

## [0.7-JLQ_26/09/24-v6] — SWD GLITCH PIO : impulsion PIO reset-synchronisée (EMFI + crowbar)

### Added
- **`SWD GLITCH PIO <pause_cy> <width_cy> [NOPWR] [SETTLE <ms>]`** et
  **`SWD GLITCH PIO SWEEP <p0> <p1> <pstep> <w0> <w1> <wstep> [SHOTS <n>] [NOPWR] [SETTLE <ms>]`** :
  glitch **reset-synchronisé par le moteur PIO** (impulsion **sub-µs**, franche), au lieu de
  l'affaissement lent INTERNAL. Le PIO s'arme sur le **front de relâchement de nRST** (`GP15 → GP3`,
  `TRIGGER GPIO RISING`) et tire l'impulsion `pause` **cycles** (6,67 ns) après, `width` cycles de
  large, sur **GP2** (trigger EMFI, ex. FaultyCat) **et GP11** (crowbar si `TARGET POWER EXT`). Puis
  l'oracle flash/SRAM. Sweep 2D `pause × width`, arrêt au 1er SUCCESS.
- ★ **Pourquoi c'est la bonne voie sur BAT32** : le LVD et le POR exigent tous deux une excursion
  **≥ 300 µs** (datasheet §6.8.5/§6.8.6) — une impulsion sub-µs **passe dessous** et peut corrompre la
  relecture flash d'`OCDEN` sans reset propre, contrairement au sag lent (~140 µs) qui ne fait que
  déclencher un brownout. Un EMFI (qui n'attaque pas VDD) échappe de toute façon au LVD/POR.

### Notes / limites
- ⚠ **Nécessite le strap `GP15 → GP3`** (câblage du banc `docs/08`) ; sans lui, aucun front ne
  déclenche le PIO — le 1er tir échoue « no trigger seen on GP3 » et le sweep s'arrête net.
- ⚠ **raiden tient le TEMPS + l'ORACLE, l'injecteur tient la PUISSANCE** : pour l'EMFI, le **FaultyCat
  doit être armé (fire) séparément** — raiden ne génère que le trigger reset-synchronisé. Le crowbar
  GP11 (variante B, `TARGET POWER EXT`) est 100 % raiden.
- `pause`/`width` sont en **cycles PIO** (6,67 ns), comme `SET PAUSE`/`SET WIDTH`. L'oracle réutilise
  `bat32_glitch_oracle_after_dip` (fix STICKYERR inclus).

### Changed
- Version : `v0.7-JLQ_26/09/24-v5` → `v0.7-JLQ_26/09/24-v6`.

## [0.7-JLQ_26/09/24-v5] — SWD GLITCH : option `SETTLE <ms>` (settle rail réglable)

### Added
- **Option `SETTLE <ms>`** sur `SWD GLITCH` et `SWD GLITCH SWEEP` : règle le settle du rail après le
  power-on, **dans le power-cycle du chemin glitch uniquement** (défaut `100`, **`0` = aucun**). Ne touche
  **pas** le `POWER_ON_SETTLE_MS` global (auto-power-on avant `SWD CONNECT`/`TARGET SYNC`), qui reste à 100 ms.
  - En `NORST` + power-cycle, `SETTLE 0` place le creux **juste après le power-on** (au lieu de settle+delay)
    → utile pour un tir calé sur la sortie de POR.
  - En nRST-synced, `SETTLE` réduit le temps mort par tir avant le relâchement de nRST (⚠ trop bas = risque
    de relâcher sur un rail pas stabilisé).
- L'en-tête imprimé indique `settle=<ms>ms` (0 quand `NOPWR`, le settle ne s'appliquant qu'au power-cycle).

### Changed
- Version : `v0.7-JLQ_26/09/24-v4` → `v0.7-JLQ_26/09/24-v5`.
- Signatures : `target_bat32_glitch_sync[_sweep]()` prennent un `uint32_t settle_ms` supplémentaire ;
  nouvelle constante `SWD_GLITCH_DEFAULT_SETTLE_MS` (100).

## [0.7-JLQ_26/09/24-v4] — SWD GLITCH : option `NORST` (glitch sans reset)

### Added
- **Mot-clé `NORST`** sur `SWD GLITCH` et `SWD GLITCH SWEEP`, symétrique de `NOPWR` : ne touche **pas**
  nRST (ni assert ni release). Le creux est alors calé sur l'**ouverture de la fenêtre**, pas sur un
  front de reset (donc **pas de reset-sync**). Quatre combinaisons désormais possibles :
  - défaut (nRST + power-cycle) : creux **synchronisé au relâchement de nRST** — la voie OCDEN ;
  - `NOPWR` : idem, sans power-cycle (rail alimenté une fois) ;
  - `NORST` : creux sur cible non resetée après `delay`, rail power-cyclé (creux **tardif**, ~settle+delay) ;
  - `NOPWR NORST` : creux **libre** sur la cible qui tourne, `delay` après l'ouverture de la fenêtre.
- L'en-tête imprimé indique les deux drapeaux (`power-cycle`/`NOPWR`, `nRST-synced`/`NORST`).

### Changed
- Version : `v0.7-JLQ_26/09/24-v3` → `v0.7-JLQ_26/09/24-v4`.
- Signatures : `target_bat32_glitch_sync[_sweep]()` prennent un `bool use_nrst` supplémentaire.

### Note
- ⚠ `NORST` **désactive la synchronisation reset** : pour l'attaque de la fenêtre OCDEN (le cas utile),
  garder nRST **actif** (défaut). `NORST` sert aux tirs libres / de comparaison, ou pour glitcher un
  état d'exécution ; combiné au power-cycle, le creux tombe bien après la fenêtre de reset (settle 100 ms).

## [0.7-JLQ_26/09/24-v3] — oracle BAT32 : ne plus confondre « verrouillé » et « SRAM perdue »

### Fixed
- **`BG_LOCKED` était inatteignable sur cible protégée.** `bat32_glitch_oracle_after_dip` (partagé par
  `SWD GLITCH[ SWEEP]` **et** `TARGET GLITCH TEST/SWEEP`) lit la flash `0x0` avant la SRAM ; à niveau
  protégé cette lecture **FAULT** et laisse **`STICKYERR`** posé dans le DP, ce qui faisait échouer la
  lecture SRAM suivante **alors que la SRAM est lisible** ⇒ chaque tir verrouillé était mal classé
  **`sram_lost`**. Mesuré au banc le 2026-09-24 : `SWD READ 0x20000008` rend une donnée en standalone,
  tandis que l'oracle rendait `sram_lost` (rail au repos, `nRST_low=N`, aucun creux). Correctif :
  `swd_clear_errors()` **entre** les deux lectures. Désormais cible protégée à SRAM lisible → **`locked`**,
  et `sram_lost` reste réservé aux vrais échecs de lecture SRAM (brownout/POR). ⚠ Corrige aussi les
  tallies faussés de `TARGET GLITCH TEST/SWEEP` (bug hérité de `bat32_glitch_shot`).

### Changed
- Version : `v0.7-JLQ_26/09/24-v2` → `v0.7-JLQ_26/09/24-v3`.

## [0.7-JLQ_26/09/24-v2] — glitch de tension synchronisé sur nRST (SWD GLITCH / SWD GLITCH SWEEP)

### Added
- **`SWD GLITCH <delay_us> <volt> [DWELL <us>] [NOPWR]`** — un voltage glitch INTERNAL **synchronisé
  sur le relâchement de nRST**, sur BAT32G135 (après `TARGET BAT32`, mode INTERNAL). Séquence par tir :
  `[power-cycle]` → maintien nRST bas → relâchement → attente `delay_us` → creux du rail
  (`power_glitch_once`) → oracle (flash `0x0` lisible + SRAM `0x20000008` vivante). Sur SUCCESS, la cible
  est laissée **sous tension + connectée** pour un dump immédiat. C'est le chemin *reset-synchronisé*
  que la limitation de `TARGET GLITCH TEST/SWEEP` (entrée précédente) signalait comme absent.
- **`SWD GLITCH SWEEP <d0> <d1> <dstep> <thr0> <thr1> <thrstep> [SHOTS <n>] [DWELL <us>] [NOPWR]`** —
  balayage **2D** : délai nRST→creux (externe) × profondeur du creux en **counts ADC** (interne),
  `SHOTS` tirs/cellule. Arrêt au 1er SUCCESS, abandon sur touche, progression throttlée. Bornes :
  200000 pts de délai × 4096 pts de seuil × 1000 shots, ≤ 2000000 tirs au total.
- Mot-clé **`NOPWR`** : ne fait que pulser nRST par tir (rail alimenté une fois au début, vitesse
  `SWD RACE`) au lieu d'un power-cycle complet — défaut = power-cycle par tir.

### Changed
- Version : `v0.7-JLQ_26/09/24` → `v0.7-JLQ_26/09/24-v2`.
- Nouveau sous-verbe `SWD GLITCH` (21e de la table `swd_subcmds`), exclu de l'auto-connexion SWD (il
  fait sa propre séquence power/reset/connect), HELP top-level + mini-help SWD mis à jour.
- L'oracle post-creux de `bat32_glitch_shot` est factorisé (`bat32_glitch_oracle_after_dip`) et partagé
  avec le nouveau chemin — comportement de `TARGET GLITCH TEST/SWEEP` **inchangé**.

### Limitation (à ne pas découvrir au banc)
- ⚠ **Synchro best-effort à l'échelle µs, pas cycle-exact.** Le creux est une opération CPU
  (`gpio_clr_mask`), pas un événement PIO ; la synchro est en C ligne droite (`busy_wait_us_32`), et le
  **préambule de `power_glitch_once`** (arm IRQ / flotte GP11-12 / sélection ADC) s'exécute *dans* la
  fenêtre chronométrée, après l'attente — un **offset fixe de l'ordre de quelques µs, non mesuré**. La
  fenêtre OCDEN ne faisant que quelques µs, mesurer cet offset à l'oscilloscope avant de conclure.
- ⚠ **Aucune campagne tirée.** Ni fenêtre, ni offset, ni profondeur atteinte mesurés : cette entrée
  apporte la *capacité de tirer synchronisé* + l'oracle, pas une défaite prouvée.
- Le power-cycle 2D coûte ~0,4 s/tir (300 ms off + 100 ms settle) : une grille large se compte en
  heures. `NOPWR` accélère mais suppose que nRST seul recharge OCDEN (non vérifié).

## [0.7-JLQ_26/09/24] — glitch de tension sur BAT32G135 (TARGET GLITCH TEST/SWEEP)

### Added
- **`TARGET GLITCH TEST <voltage> [count]` et `TARGET GLITCH SWEEP` acceptent désormais le
  BAT32G135** (après `TARGET BAT32`, en mode INTERNAL). Jusqu'ici `TARGET GLITCH` passait par
  l'auto-détection STM32 (`DBGMCU_IDCODE`) et refusait le BAT32 (`DEV_ID 0x000`, « Unknown DEV_ID »,
  mesuré au banc le 2026-09-24). Le nouveau chemin appelle la primitive agnostique
  `power_glitch_once()` (creux du rail : GP10 tiré bas, GP11/12 flottants, jaugé par l'ADC0/GP26) et
  classe avec l'oracle propre au BAT32 : la **code flash `0x0` devient lisible** ET la **SRAM
  `0x20000008` survit** = SUCCESS (protection tombée).
- Les verbes propres aux STM32/LPC (`PAYLOAD`/`BYPASS`/`LPCBYPASS`/`REGDUMP`/…) **errorent
  explicitement** pour le BAT32 (« unsupported for BAT32 ») au lieu de retomber sur l'auto-détect.

### Changed
- `TARGET GLITCH` : branche BAT32 en tête du dispatch (après `match_and_replace`), HELP mis à jour.

### Limitation (à ne pas découvrir au banc)
- ⚠⚠ **Le creux n'est PAS synchronisé sur le reset.** `power_glitch_once()` tire le rail bas pendant
  que la cible TOURNE ; il ne coïncide pas avec le chargement des octets d'option au relâchement du
  reset, là où `OCDEN` est verrouillé. Un brownout mid-run qui évite un POR laisse `OCDEN` verrouillé
  (oracle LOCKED) ; un creux assez profond pour un POR recharge les octets d'option normalement (SRAM
  perdue). Défaire la protection demande d'aligner le creux sur la fenêtre de reset — **non
  implémenté**. Ce changement apporte la *capacité de tirer* + l'oracle, pas une défaite prouvée.
- Source INTERNAL faible (**36 mA**, 3 GPIO gangués) : profondeur réellement atteinte non mesurée.
- Aucune campagne tirée : ni fenêtre, ni profondeur, ni dwell mesurés sur BAT32.

## [0.7-JLQ_26/09/18] — index d'AP explicite sur la CLI SWD

### Added
- **`SWD READ AP [<n>] <addr>` et `SWD WRITE AP [<n>] <addr> <val>`** : l'APSEL
  devient un argument optionnel. Jusqu'ici les deux commandes appelaient
  `swd_read_ap(0, …)` / `swd_write_ap(0, …)` en dur, ce qui rendait tout AP
  autre que l'AHB-AP inatteignable depuis la CLI. La couche SWD, elle, savait
  déjà viser n'importe quel AP : `swd_select_ap()` compose
  `SELECT = (ap<<24) | (addr&0xF0)` depuis toujours. Rien n'a changé dans
  `swd.c`.

  Motivation : le **CTRL-AP du nRF52** (AP ≠ 0) est le seul AP qui répond encore
  quand `APPROTECT` est armé — il porte à la fois l'état de protection et
  l'effacement de masse qui le défait. `swd_read_ap()` n'exige que
  `initialized`, pas `ahb_initialized`, donc l'état « DP seul » que laisse une
  puce protégée suffit.

- L'index se résout par le **nombre d'arguments**, jamais par la valeur :
  `SWD READ AP 1` reste `AP[0x01]` de l'AP 0, `SWD READ AP 1 0xFC` lit l'AP 1.
  Sans cette règle, une commande existante changerait de sens en silence.
  `scripts/swd_regression.py` (`swd read ap 0`, `swd read ap FC`) est couvert
  par ce choix, et la chaîne imprimée pour l'AP 0 reste `AP[0x%02X]` au
  caractère près — ce script assert dessus. Seul un APSEL non nul, qu'aucun
  appelant existant n'émet, obtient la forme élargie `AP%u[0x%02X]`.

⚠ Non vérifié sur matériel : aucun banc n'a tourné avec cette version.

## [0.7-JLQ_26/09/16] — fork JLQ : BAT32G135, course SWD, PHY sur PIO

Regroupe en une version publiable l'ensemble du travail mené en local sur les
numéros 0.8 → 0.14, plus le chantier Wi-Fi resté non publié. Le détail de
chaque étape est dans les sections ci-dessous, qui ne sont pas réécrites.

### Added
- **Cible Cmsemicon BAT32G135** (Cortex-M0+) : `TARGET BAT32`, décodage des
  niveaux de protection 0/1/2 depuis OCDEN/OCDM, et la famille
  `SWD BAT32 {RAMREAD, PROGRAM, WRITE, PATTERN, SECTORERASE, ARM, DISARM,
  CHIPERASE}`. `RAMREAD` lit la flash *à travers le cœur* : c'est la seule
  opération non destructive du lot, et la seule qui ne demande pas `CONFIRM`.
- **Couche physique SWD sur PIO2** : `SWD PHY [BITBANG|PIO [<khz>]]` et
  `SWD BENCH`. Le bit-bang a un délai par bit qui écrase la fenêtre de course —
  `SWD RACE` refuse donc de tourner sur autre chose que PIO.
- **Course au relâchement de reset** : `SWD RACE`, `SWD RACE SWEEP`,
  `SWD RACE PERSIST`, contre l'écriture de `DBGSTOPCR.SWDIS` par le firmware
  de la cible.
- **Option `RAIDEN_CONSOLE_UART`** (OFF par défaut) : la CLI aussi sur UART0
  GP0/GP1. Exclusion mutuelle franche avec le ChipSHOUTER, qui possède les
  mêmes broches — `CS` refuse alors explicitement au lieu d'écrire dans le vide.
- **`BOARD=pico2w`** : CLI TCP sur Wi-Fi, en `lwip_poll` et non
  `threadsafe_background` — cette dernière ferait tourner lwIP depuis une
  interruption de fond, exactement l'asynchronisme qu'un firmware de glitch ne
  peut pas tolérer près de sa fenêtre de tir.
- **Outillage hôte** : `bat32_{dump,restore,dataflash,ramread_compare,
  race_sweep,l1_ramread_test}.py` et `raiden_bridge.py` (relais TCP/pty).

### Fixed
- `pio_alloc` réserve les ressources PIO câblées en dur **avant** toute autre
  initialisation. Sans cela le pilote CYW43 vole sa state machine à `swd_phy`
  et rien ne le signale jusqu'à la première commande SWD.
- La LED passe par `RAIDEN_LED_{INIT,SET}`. Sur Pico 2 W, GP25 est le
  chip-select du CYW43 et non la LED ; `pico2_w.h` ne définit délibérément
  aucun `PICO_DEFAULT_LED_PIN`, si bien qu'un `#define LED_PIN 25` en dur
  compilait sans le moindre avertissement tout en tuant la liaison Wi-Fi.
- `tests/conftest.py` draine le lien série pendant l'attente au lieu de dormir
  en aveugle. Mesuré le 2026-09-05 : `HELP` émet 7828 octets, une attente
  aveugle de 5 s en recevait 4888 (coupée dans `== ADC ==`) parce que le tampon
  TX de l'USB CDC se remplissait sans lecteur et que `stdio_usb` jetait le
  reste.

### ⚠ Ce qui n'est PAS vérifié
- **Le firmware de cette branche n'a pas été compilé** : aucun `PICO_SDK_PATH`
  sur la machine où elle a été assemblée. Aucun `.uf2` n'en est issu, donc rien
  ici n'a été flashé ni relu par `VERSION`.
- **Les broches GP0/GP1** de l'option console : jamais câblées. Le test à un
  fil qui tranche est de relier GP0 à GP1 et d'envoyer `VERSION` par l'USB — la
  CLI parse alors sa propre réponse et doit rendre
  `ERROR: Unknown command 'Raiden'`, ce qui prouve les deux sens du lien.
- **La variante Wi-Fi** : écrite et câblée au build, jamais exécutée sur une
  Pico 2 W.

## [0.14] — 2026-09-05 — console CLI sur UART0 (GP0/GP1), en option

### Statut : **compilé, flashé et vérifié sur le banc** — sauf le lien UART lui-même
`VERSION` rend `v0.14` + `Console: USB CDC + UART0 GP0/GP1 @115200 (ChipSHOUTER
disabled)`, `CS STATUS` et `CS ARM` renvoient l'erreur explicite, `PINS` nomme le
bon propriétaire de GP0/GP1, et la CLI répond par les trois chemins hôte (USB
direct, `socket://` et pty via `raiden_bridge.py`).

⚠ **Ce qui n'est PAS vérifié : les broches GP0/GP1 elles-mêmes.** Aucun
adaptateur USB-UART ni FaultyCat n'était câblé au banc. Le test à un fil qui
tranche : **relier GP0 à GP1** (bouclage), puis envoyer `VERSION` par l'USB — la
réponse repart par GP0, revient par GP1 et la CLI parse sa propre sortie, ce qui
doit produire `ERROR: Unknown command 'Raiden'`. Cette erreur-là **prouve les
deux sens** du lien.

### Added
- Option CMake **`RAIDEN_CONSOLE_UART`** (OFF par défaut) :
  `pico_enable_stdio_uart(raiden_pico 1)` + `-DRAIDEN_CONSOLE_UART=1`. Le pilote
  stdio du SDK multiplexe USB et UART — **aucun code métier à toucher**,
  `src/uart_cli.c` est inchangé. Build :
  `cmake -S . -B build -DBOARD=pico2 -DRAIDEN_CONSOLE_UART=ON`.
- `VERSION` affiche désormais **le transport de la console** sur une seconde
  ligne. C'est le seul moyen, depuis l'hôte, de savoir quelle variante de binaire
  est réellement sur la puce — et c'est ce que le nouveau test utilise pour
  rester valable contre les deux builds.

### ★ Exclusion mutuelle avec le ChipSHOUTER — et pourquoi elle est franche
`UART0`/`GP0`/`GP1` est **la** liaison ChipSHOUTER (`CHIPSHOT_UART_ID uart0`,
`config.h`) et **aussi** le `PICO_DEFAULT_UART` que `pico_enable_stdio_uart`
utilise sur Pico 2. Les deux ne peuvent pas posséder le même périphérique :

- `chipshot_uart_init()` n'est pas appelé, et surtout **`chipshot_uart_process()`
  non plus** — sans quoi il consommerait les octets tapés par l'opérateur sur la
  console ;
- la commande **`CS` refuse explicitement** (`ERROR: CS unavailable - console
  UART owns UART0 (GP0/GP1)`) au lieu d'écrire dans le vide. Une commande de
  glitch qui ne part pas sans le dire est exactement ce que la discipline CLI du
  dépôt interdit ;
- `PINS` et `HELP` nomment le propriétaire réel dans chaque variante.

### ⚠ Le piège à connaître avant d'utiliser cette console
Le pilote `stdio` UART du SDK est **bloquant** : attente active sur la FIFO TX,
~87 µs par octet une fois pleine à 115200. Or `target_uart.c:1513` documente,
mesures à l'appui, que quelques instructions de dispatch suffisent à rater la
fenêtre de restauration du rail dans la boucle ADC. D'où la ligne de partage :

- ✅ **le moteur de glitch PIO est immunisé** (il part du front matériel
  GP15 → GP3, l'état du CPU lui est indifférent) — la campagne BAT32 ne risque
  rien ;
- ❌ **`VMIN` / ADC-gated ne l'est pas** : console silencieuse ou désactivée
  pendant tout `SET VMIN`, ou à traiter comme le réseau (`net_quiet_*`).

### Added — tests
`tests/test_config_none.py` : `test_version_reports_console_transport`,
`test_cs_matches_console_transport` (les **deux** branches sont assertées, donc
le test a du sens contre l'une comme l'autre variante) et
`test_pins_names_the_uart0_owner`. `test_cs_voltage_out_of_range` et
`test_cs_pulse_out_of_range` deviennent conscients de la variante par l'aide
`_console_owns_uart0()` : leur assertion `"range" in r` ne peut pas tenir quand
la commande refuse en amont pour cause de propriétaire d'UART0.

### ★ Fixed — `conftest.py` dormait pendant que la CDC débordait
`RaidenClient.cmd()` faisait `write()` puis **`time.sleep(wait)` sans lire**,
puis drainait. Sur une réponse longue, le tampon TX du CDC côté cible se
remplit sans lecteur, `stdio_usb` atteint `PICO_STDIO_USB_STDOUT_TIMEOUT_US` et
**jette la suite, en silence, au milieu d'une ligne**.

Mesuré sur `HELP` (2026-09-05, firmware v0.14) :

| Lecture hôte | Octets reçus | Sections |
|---|---|---|
| `sleep(5)` puis drain (ancien) | **4888** | 9 — coupé dans `== ADC ==` |
| drain **pendant** l'attente (nouveau) | **7840** | **15** — complet |

Le firmware émet 7828 octets d'aide (8398 dans la variante par défaut) et n'en
a jamais perdu un seul : **c'était le harnais**. `cmd()` draine désormais
pendant la fenêtre d'attente, la queue « jusqu'à 0,5 s de silence » est
inchangée. C'est ce qui faisait échouer `test_help_has_sections`, dont la
docstring soupçonnait le firmware à tort.

⚠ Corollaire pour l'exploitation : **tout client qui dort avant de lire perd
des octets** sur les réponses longues de ce firmware. Les scripts du dépôt
utilisent déjà l'idiome `read(4096)` en boucle et ne sont pas concernés.

### Résultats de la suite, sur cible BAT32 câblée (2026-09-05)

```
pytest tests/ -q --config=swd   ->  16 failed, 153 passed, 48 skipped (12:48)
```

⚠ **Il faut `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`** sur cette machine : les
plugins système `salt.config` et `black` déclarent des hooks incompatibles avec
pytest 9.1.1 et font échouer le démarrage de pytest lui-même (pas les tests).

★ **Les 16 échecs sont exactement la ligne de base déjà documentée dans
l'entrée *Unreleased*** — 14 + 1 + 1, mêmes fichiers, mêmes causes. **Aucun
n'est imputable à v0.14**, et `test_help_has_sections`, qui échouait avant,
passe maintenant :

| Nombre | Où | Cause, vérifiable |
|---|---|---|
| **14** | `test_config_swd_bl.py` | exigent un **STM32 avec bootloader ISP UART** ; la cible câblée est un BAT32, qui n'en a pas (`No response from bootloader`) |
| **1** | `test_config_none.py::test_target_bat32_swd_opt_no_wiring_fails_at_connect` | sa docstring pose « no target wired », or une cible SWD **est** câblée : `SWD OPT` réussit là où le test attend une erreur |
| **1** | `test_config_swd.py::test_bat32_flash_erase_refused` | attend le libellé « read-only », changé en **v0.11** ; le refus fonctionne — `ERROR: BAT32G135 flash writes are under SWD BAT32, not SWD FLASH` — seul le texte diffère |

Les tests destructifs (`--destructive`) n'ont **pas** été exécutés.

⚠ **Le Pico a quitté le bus USB après la campagne de tests** — absent de `lsusb`,
pas seulement de `/dev/ttyACM0`. Troisième occurrence connue de l'instabilité
déjà consignée au §9sexies de `TPLink_Tapo/07_BAT32G135_FAULTYCAT.md`.
**Chronologie mesurée** : la suite s'est terminée normalement, un `VERSION` a
répondu après elle, puis la carte a disparu **au repos** — pas sous charge.
Aucune conclusion tirée sur la cause. Reprise : débrancher/rebrancher l'USB
(pas de FTDI sur ce poste, donc `scripts/reset_pico.py --wait` est indisponible).

★ **La flash de la cible n'est pas en cause et n'a pas été touchée** : les tests
destructifs sont restés ignorés, les seules écritures SWD de la suite visent la
**SRAM** (`0x20000000`), et `SWD OPT` rendait `Level 0 / OCDEN=0xFF` avant comme
après. Un reset de la cible efface les motifs de test laissés en SRAM.

### ⚠ Deux constats de banc, non corrigés ici
- **`CLAUDE.md` annonce `PICO_SDK_PATH=/home/software/unpacked/pico-sdk`, qui
  n'existe pas sur cette machine.** Le SDK réellement utilisé est celui
  **vendoré dans le dépôt** : `raiden-pico/pico-sdk` (c'est ce que porte
  `build/CMakeCache.txt`).
- **La carte du banc n'est pas une Pico 2 stock.** `STATUS` lit `PACKAGE_SEL` et
  rapporte **RP2350B, QFN-80, 48 GPIOs, PSRAM** — une Pico 2 est une RP2350A
  QFN-60. Le firmware est pourtant construit en `BOARD=pico2` et fonctionne.
  **Aucune conclusion tirée ici** : changer `BOARD` déplacerait des affectations
  de broches, ce n'est pas une décision à prendre en passant.

## [Unreleased] — CLI TCP sur Wi-Fi (Pico 2 W) — étapes 1 et 2

### Statut : étapes 1 et 2 **validées sur le banc**, la suite attend un Pico 2 W
Firmware flashé et exercé sur le Pico 2 : `SWD CONNECT` bit-bang et PIO,
`SWD RACE` (`SUCCESS`, SP=0x20000E10 PC=0x000001A9) et `SWD RACE PERSIST`
(`READ_OK ACK=0x1`) — c'est-à-dire précisément ce que le pré-claim PIO et le
préchargement nRST pouvaient casser.

**Le même dump 64 Ko par les trois chemins donne le même md5** — USB direct,
`socket://` via le pont, et pty via le pont : `6f37bd86…` (code) et `4a5114b8…`
(data), 26 s par passe dans les trois cas.

Suite de tests : **150 passés, 48 ignorés, 16 échecs**. Aucun ne semble
imputable à ces changements — mais c'est une **attribution argumentée, pas une
mesure** : il n'existe aucun firmware de référence committé d'avant cette
session contre lequel rejouer la suite (l'arbre porte v0.11 à v0.13 non
commitées), et reconstruire HEAD donnerait un binaire antérieur à `SWD BAT32`,
donc sans valeur comparative. Les trois causes, chacune vérifiable dans le code
ou le test lui-même :

- **14** dans `test_config_swd_bl.py` : exigent une cible **STM32 avec
  bootloader ISP UART**. La cible câblée est un BAT32, qui n'en a pas — le
  firmware l'annonce lui-même, et l'échec dit « NO RESPONSE FROM BOOTLOADER ».
- **1** dans `test_config_none.py` : sa propre docstring pose « No target is
  wired in config_none », alors qu'une cible SWD est câblée sur ce banc, si
  bien que `SWD OPT` réussit là où le test attend une erreur.
- **1** dans `test_config_swd.py` : attend le libellé « read-only », changé en
  **v0.11** quand `SWD FLASH` s'est mis à rediriger vers `SWD BAT32`. Le refus
  fonctionne, seul le texte diffère — et `src/command_parser.c`, qui produit ce
  message, n'a pas été modifié dans cette session.

Non testé faute de matériel : tout le Wi-Fi (aucun Pico 2 W câblé). La baseline
VMIN/TRACE (étape 0 du plan) n'est pas prise — elle exige le rail cible sur
GP26. Plan complet : `~/.claude/plans/typed-nibbling-hearth.md`.

### Added — côté hôte, mesuré
- `scripts/raiden_bridge.py` — pont réseau dans les deux sens, sans dépendance
  système (remplace `socat`, donc pas de `sudo`) : `serve` expose un port série
  en TCP (permet d'exercer tout le chemin hôte **avant** que le firmware ne
  parle TCP), `pty` expose une session TCP comme pseudo-terminal local.

### ★ Fixed — `read(in_waiting)` est inutilisable sur une URL `socket://`
Sur un socket, `in_waiting` de pyserial renvoie `len(select(...))` — **0 ou 1,
jamais un nombre d'octets** (`serial/urlhandler/protocol_socket.py:136-142`). Or
`bat32_dump.py` et `bat32_race_sweep.py` faisaient `read(in_waiting)` suivi d'un
`sleep(0.02)`.

Mesuré contre une réponse simulée de 8 Ko :

| Idiome | Débit | Résultat |
|---|---|---|
| `read(in_waiting)` + `sleep(0.02)` | **49 o/s** | **tronqué en silence** |
| `read(4096)` + settle | 40 500 o/s | complet |
| `read(in_waiting)` **via le pont pty** | 201 000 o/s | complet |

Vérifié ensuite sur le banc réel, dump 64 Ko complet : mêmes md5 et mêmes 26 s
par les trois chemins. Deux réglages ont été nécessaires pour y arriver, tous
deux mesurés : le `read()` de fin de réponse prend un timeout court (sinon
+38 s par dump), et le timeout du port descend à 0,05 s — `serial.read(n)` rend
la main sur *n octets ou le timeout*, jamais « dès qu'il y a des données », ce
qui doublait la durée.

Les deux boucles passent donc sur l'idiome `read(4096)` déjà utilisé par
`bat32_restore.py` — au passage, cela supprime le `sleep(0.02)` inconditionnel
qui plafonnait aussi le débit USB. Les cinq scripts ouvrent maintenant leur port
par `serial_for_url()`, qui accepte indifféremment `/dev/ttyACM0` et
`socket://hôte:port`, avec `write_timeout=5` (le défaut `None` bloque à l'infini
sur un socket).

### Added — firmware : les trois pièges du portage Pico 2 W, corrigés d'avance
- `src/pio_alloc.c` — `pio_resources_reserve()` réclame explicitement les 7 state
  machines câblées en dur. Sans elle, le pilote CYW43 (qui itère les PIO en ordre
  **décroissant**) prendrait **pio2 SM0**, celle de `swd_phy` : tout marcherait
  jusqu'à la première commande SWD.
- `swd_phy_preload_programs()` (`src/swd_phy.c`) — le programme PIO nRST n'était
  chargé qu'au **premier `SWD RACE`**, depuis la section chronométrée : la
  panique aurait été différée jusqu'à cette commande.
- `src/main.c` — la LED passe par une abstraction : sur Pico 2 W, GP25 n'est pas
  une LED mais le **chip-select du bus Wi-Fi**, et `pico2_w.h` ne définit
  délibérément aucun `PICO_DEFAULT_LED_PIN`, donc l'ancien `#define LED_PIN 25`
  aurait compilé sans warning en tuant la liaison.
- `include/net_cli.h` — inclus **inconditionnellement** ; hors Wi-Fi tout se
  réduit à des fonctions vides, ce qui permet d'écrire les sites d'appel sans un
  seul `#ifdef` dans le code métier. Preuve : binaire `pico2` inchangé.
- `src/net_cli.c` + `include/lwipopts.h` — Wi-Fi STA, poll, sections
  silencieuses. `pico_cyw43_arch_lwip_poll` et **non** `_threadsafe_background`,
  qui exécuterait lwIP depuis une interruption de fond.
- `CMakeLists.txt` — `cmake -S . -B build -DBOARD=pico2w`.

### Added — discipline temps réel : le glitch prime
Même en mode poll, le pilote CYW43 arme une interruption GPIO **de niveau** sur
son host-wake, sur `IO_IRQ_BANK0` — le vecteur dont `target_uart.c:1513`
documente que quelques instructions de dispatch suffisent à faire rater la
restauration du rail. `NET_QUIET_SECTION` masque **cette broche seule** (jamais
`IO_IRQ_BANK0` entier, qui porte aussi nRST et GLITCH_FIRED) et suspend le poll ;
l'interruption étant sur niveau, elle se redéclenche au réarmement — rien n'est
perdu. L'attribut `cleanup` garantit la sortie malgré les `return`.

Appliquée à : la fenêtre ADC de `power_glitch_once` (**sans** la surveillance
nRST de 50 ms, qui affamerait le lien), et les deux variantes de `SWD RACE`.
Dans `swd_race_once` la section s'ouvre **après** le diagnostic de câblage nRST,
volontairement : ce `printf` distingue « nRST mal câblé » de « cible
verrouillée » et ne doit jamais pouvoir être jeté. Point de relance entre deux
tirs de sweep. Reste à faire : la section TRACE.

## [0.13] — 2026-09-01 — `SWD BAT32 RAMREAD`: read flash through the core

### Added
- `SWD BAT32 RAMREAD <addr> [words]` → `swd_bat32_ram_read()` in
  `src/swd.c`. Reads flash **through the target core**, sidestepping the
  debugger-side flash block that Level 1 applies. It halts the core, writes
  a 16-byte Thumb copier into SRAM at `0x20000000`, sets `r0`=source,
  `r1`=`0x20000020` (buffer), `r2`=word count, `SP`=`0x20002000`,
  `PC`=payload|1, resumes, waits for the trailing `BKPT` to re-halt, and
  reads the buffer back over SWD. 1024 words (4 KB) per pass.

  The copier, assembled by hand (ARMv6-M Thumb-16):
  `6803 ldr r3,[r0]` · `600B str r3,[r1]` · `3004 adds r0,#4` ·
  `3104 adds r1,#4` · `3A01 subs r2,#1` · `D1F9 bne -14` · `BE00 bkpt`.

  Unlike every other `SWD BAT32` verb this one is **read-only on flash**, so
  it does not take `CONFIRM` — but it **overwrites target SRAM
  `0x20000000-0x2000101F`**, which is where the `.data` copied out of flash
  at boot lives. At Level 1, dump SRAM *before* the first RAMREAD.

### ★ Measured at Level 0 — the whole mechanism works
The three premises that were *not* verified when this was written are now
verified on the bench BAT32G135: **SRAM is writable**, `PC`/`SP` writes via
`DCRSR`/`DCRDR` **are accepted**, and this ARMv6-M **does execute from
SRAM**. Both paths agree byte for byte:

| Region | RAMREAD (via core) | `SWD READ` (via debugger) |
|---|---|---|
| code flash, 64 KB @ `0x0` | `2d03db71e805d1c14b8389893feec4ed` | identical |
| data flash, 1536 B @ `0x500000` | `4a5114b8114f61b1c8eb03537406a3cf` | identical |

**35 s** for the 64 KB pass at `SWD SPEED 4` (the 70 s figure in the compare
harness covers both paths interleaved). **What is still untested is the only
thing that matters for the bypass: whether this works at Level 1.**

⚠ That code-flash md5 is **not** the reference `6f37bd86…` of 2026-08-31:
20 945 bytes of the bench part now read `0xFF` over `0x3340-0x454F` and
`0x8000-0xC15F`. Both read paths agree on that, so it is the part that
changed, not the reading — the post-chip-erase restore did not put
everything back. `assets/bat32_dump_20260831/bat32_code_flash.bin` still
held the intact image, and the part was **restored from it the same day**:
84 blocks of 256 bytes in 203 s (an 85th had gone in during an
earlier, interrupted attempt), read back over the debugger path as
`6f37bd86c41a75c19db65cc5824f7199` — byte-identical to the original. The
degraded state is kept in `assets/bat32_dump_20260901/` as a record.

### Fixed — ★ 1 KB TAR window: `swd_read_mem()`/`swd_write_mem()` silently wrapped
ADIv5 only guarantees TAR auto-increment **inside a 1 KB window**; past the
boundary the AP wraps back to the start of that window and re-serves data
already transferred, with no error anywhere. Both functions armed TAR once
and then looped over `DRW`, so **any transfer crossing a 1 KB boundary
returned wrong data** — the first 4 KB `RAMREAD` was correct up to offset
`0x3E0` (= `0x20000400`) and repeated itself from there.

This was not specific to RAMREAD: every caller passing a range that crosses
a 1 KB boundary was affected. It went unnoticed because `SWD READ` chunks
by 64 words from an aligned base, and the validated 64 KB dumps happen to
start aligned. Both functions now re-arm CSW+TAR at every window crossing.

### Added — diagnostics that make a Level 1 failure legible
- `DEMCR` now sets `VC_HARDERR` alongside `TRCENA`: a payload that faults
  halts immediately instead of surfacing as the same 500 ms timeout as
  "the core never ran".
- After the halt, `PC` must be on one of the two trailing `BKPT`s
  (payload+`0x0C`/+`0x0E`); any other `PC` is reported with `DFSR` and the
  buffer is refused rather than returned as if it were flash.
- `S_LOCKUP` is distinguished from "still running" in the timeout message.
- A misaligned `<addr>` is rejected up front, in the parser and in
  `swd_bat32_ram_read()` — `ldr r3,[r0]` would fault on ARMv6-M and read
  as "the bypass does not work".

### ★★ Found — `CHIPERASE` does NOT erase the data flash, and `SECTORERASE` is what does
Measured 2026-09-02. After a `SWD BAT32 CHIPERASE CONFIRM` that reported
success and left the code flash uniformly `0xFF`, `0x00500008` still held the
sensor's pairing record (`AA 55 AA 55 "device_id"`), **byte-identical to the
pre-erase dump**. Re-triggering the same erase with its dummy write aimed at
`0x00500000` instead of `0x00000000` changed nothing: the data flash array is
simply outside what `FLERMD=0x08` covers.

This contradicts the firmware's own message ("code + data flash"), this
changelog's v0.11 entry, and §7.4 of `TPLink_Tapo/07_BAT32G135_FAULTYCAT.md`.

**What it changes for Level 1:** the only documented way out of Level 1 is the
chip erase — and it now turns out that escape **preserves the data flash**.
The pairing, and anything else living at `0x500000`, survives the round trip.
That is a materially better position than "Level 1 costs you the pairing".

`CHIPERASE` keeps erasing the code flash only, deliberately: escaping Level 1
costs a chip erase, and the part happens to keep its data flash through it —
destroying that on the caller's behalf would throw away a pairing the hardware
was willing to preserve. The CLI help and the progress line say so now.

### Added — `SWD BAT32 SECTORERASE <addr> CONFIRM`
`swd_bat32_sector_erase()` in `src/swd.c` — the vendor driver's `FLERMD=0x10`,
documented in the v0.11 entry as existing but never wired up. It is the **only**
way to blank the data flash. `<addr>` is any address inside the sector to erase;
erasing a code-flash sector works the same way and is equally destructive, hence
`CONFIRM`.

**Measured: the data-flash sector is 512 bytes.** One erase at `0x500000`
blanked `0x500000-0x5001FF` exactly; the 1.5 KB array takes three
(`0x500000`, `0x500200`, `0x500400`).

### ★ Measured — the data-flash write path works
`bat32_dataflash.py restore` put the 2026-09-01 image back onto a freshly
blanked data flash and read it back after a reset as
`4a5114b8114f61b1c8eb03537406a3cf` — exact. The probe-block mechanism did its
job on the way (first block written and verified alone before the rest). Until
this run, no byte had ever been programmed at `0x500000` by this firmware.

### ★ Found — a BAT32 flash read with the core RUNNING returns prefetch, not memory
Measured 2026-09-02 on an unmodified part: with the core running, `0x00004910`
read back `70 47 C0 46` (`bx lr; nop` — code the core was executing), and
`FF FF FF FF` immediately after `SWD HALT`, reproducibly, three passes. A halt
cannot erase flash: the *read* is what was wrong. Intermittently, a debugger
read is served the core's prefetch instead of the requested address.

Consequences, all confirmed on the bench:
- A `bat32_restore.py` diff taken with the core running invented 9 changed
  bytes across 3 blocks, one of them flagged as needing an erase (bits 0→1).
  With `SWD HALT` first, the same part shows **exactly 1 changed byte** — a
  deliberate edit by the operator. The whole "the part has drifted again"
  alarm was the read, not the part.
- The four "corrupted bytes at `0x4664`" that `bat32_dump.py`'s verification
  pass once caught were almost certainly this, not a flaky link.
- The v0.13 validation above was **not** affected: RAMREAD halts the core and
  its payload ends on `BKPT`, so the core stays halted for everything after
  the first pass — which is exactly why both paths agreed across 64 KB.

`bat32_dump.py` (`--no-halt` to opt out), `bat32_restore.py` and
`bat32_ramread_compare.py` now halt the core before reading anything.
`bat32_restore.py` additionally **resets the target before its verification
read**: a read chained straight onto programming can be served by the flash
controller's buffer and confirm a value the cell does not hold.

⚠ The `6f37bd86…` reference image was dumped **before** this was known, i.e.
with the core running. Three passes agreed (two bitbang + one PIO), which
argues the artifact did not hit them — but that is not the same as proof.
Treat it as a caveat on the reference until a halted re-dump confirms it.

### Added — host-side harnesses
- `scripts/bat32_ramread_compare.py` — the reproduction for the table above.
  `--mode compare` demands RAMREAD and `SWD READ` agree (Level 0, tests the
  mechanism); `--mode ref` compares against a reference image, for Level 1
  where `SWD READ` faults and no second path exists.
- `scripts/bat32_dataflash.py` — `backup` / `restore` for the **data flash**
  (`0x00500000`), where the sensor's pairing lives; a chip erase (the only way
  out of Level 1) takes it too. Backup reads twice and refuses if the passes
  disagree. Restore excludes the `OCDM`/`BTEN` block by default and refuses
  outright to write `OCDM = 0x3C` (Level 2, no way back), and — since the write
  path has never been exercised at `0x500000` — writes **one probe block
  first**, verifies it after a reset, and only then writes the rest.
  Measured while testing it: the data flash is **live** — 12 bytes changed in
  ten minutes of normal operation (counters plus a journal at `0x00500453`),
  and some increments raise bits back to 1, so the sensor erases pages itself.
- `scripts/bat32_restore.py` — restores code flash from an image, writing only
  the blocks that differ. It refuses up front any block needing a 0→1 bit
  (programming only clears bits; that case needs an erase) and only reports
  success **after reading the part back**. This is what the 2026-09-01 restore
  lacked: it left 20 945 bytes at `0xFF` and nobody saw it.

### Fixed — `scripts/flash.sh` could not find the bootloader volume
It looked only under `/media/$USER`; udisks2 mounts under `/run/media/$USER`,
and with no session automounter nothing mounts at all. It now checks both
and mounts the `RP2350`-labelled volume itself via `udisksctl` (no root).
It also waited only 0.5 s after opening the CDC port before sending
`REBOOT BL` — but opening that port restarts the firmware, so the command
landed mid-init and was dropped. It now waits for the CLI to answer first.

## [0.12] — 2026-09-01 — `SWD RACE PERSIST`, and what it proved about Level 1

### Added
- `SWD RACE PERSIST <delay_us> [ADDR <hex>]` (`swd_race_persistent()` in
  `src/swd.c`). `SWD RACE` re-establishes everything after releasing nRST —
  line reset, JTAG-to-SWD, DPIDR, AHB-AP bring-up — so its first memory
  access lands ~250 µs after the reset edge, far too late for §8.3's
  *window A*. This variant bets that nRST resets the core and system but
  **not** the SW-DP/AHB-AP: it pre-loads CSW and TAR *before* touching
  nRST, then issues only the DRW read afterwards. First access lands within
  microseconds instead of hundreds.
- `ADDR` lets the probe target any address, which is what turned up the
  SRAM result below.

### Measured — the bet pays off, and window A is unwinnable anyway
**The DP and AHB-AP do survive nRST** (`ACK=0x1` even at delay 0), so the
short sequence works. With it:

| Delay after nRST release | Level 0 | Level 1 |
|---|---|---|
| 0 → 440 µs | `READ_OK`, `0x00000000` | `READ_OK`, `0x00000000` |
| 450 µs onward | `READ_OK`, **`0x20000E10`** | **`FAULT` (ACK=0x4)** |

Sharp transition between 440 and 450 µs, 9 shots each side, no scatter.
**No sliver exists** where a protected part returns real data: protection
latches exactly when memory becomes accessible. Shortening the sequence
further cannot help — arriving earlier simply yields nothing.

⚠ **Correction to the v0.11 entry's reading.** That "zeros before 450 µs"
window is *not* a flash macro waking up: **SRAM returns zeros there too**,
at both protection levels. It is the whole system held in reset — the debug
AP survives, but the AHB bus behind it answers nothing.

### ★ SRAM is fully readable at Level 1 — protection covers flash only
Same run, same instant (600 µs after reset release): flash `0x0` faults
(`ACK=0x4`) while **SRAM `0x20000008` reads back its real value**
(`0xE864F825`), and a direct 64-byte read of `0x20000000` returns content
identical to what Level 0 shows. The §2.3 truth table only ever restricted
"données flash"; that is now confirmed to be literal.

This matters because C startup copies `.data` **out of flash into SRAM at
every boot** — so flash-derived content is recoverable at Level 1 without
ever reading flash. Combined with the fact that the core can still be
halted at Level 1, the untested next step is injecting a payload into SRAM
and letting the core (which *is* allowed to read flash) copy it out. See
§2bis.5 of `TPLink_Tapo/07_BAT32G135_FAULTYCAT.md`.

## [0.11] — 2026-09-01 — BAT32G135 flash writes, and a full Level 1 round trip

### Added — `SWD BAT32 <op> ... CONFIRM`
`PROGRAM <addr> <byte>` · `WRITE <addr> <hex>` (256 B/command) ·
`PATTERN <addr> <len>` (blank-only bulk test) · `ARM` (OCDEN → 0xC3) ·
`DISARM` (OCDEN → 0x83) · `CHIPERASE`. Every form requires a literal
`CONFIRM` as the last token, and all argument validation happens before any
hardware is touched. `SWD FLASH` now redirects BAT32 here instead of
refusing outright; `SWD RDP` still refuses (wrong protection model).

Backing functions `swd_bat32_flash_program()` / `swd_bat32_chip_erase()`
(`src/swd.c`) are transcribed from **Cmsemicon's own `Driver/src/flash.c`**
in the official CMSIS pack (`Cmsemicon.BAT32G135.1.0.4.pack`), which is
authoritative and **corrects `07_BAT32G135_FAULTYCAT.md` §7.4**:

| | Doc §7.4 | Vendor driver |
|---|---|---|
| Program granularity | "mot 32 bits" | ★ **byte** — `FLOPMD1/2` re-armed before *every* byte |
| `FLERMD` | `0x8` chip erase | `0x08` ✓, and **`0x10` = sector erase** (absent from doc) |
| After the operation | — | **`FLERMD=0x00`**, **`FLPROT=0xF0`** (re-lock) |
| Timing registers | — | `FL*CNT` never touched; resets are usable |

Byte granularity is what let OCDEN be armed **without disturbing the
WDT/LVD/HOCO bytes sharing its 32-bit word**.

### Fixed
- `swd_bat32_flash_program()` / `_chip_erase()` clear sticky errors first.
  At Level 1 a flash read returns FAULT (ACK=0x4), latching `STICKYERR`,
  after which **every** AP transaction fails until `DP_ABORT`. Without the
  clear, the CLI's own read-back probe poisoned the link and the subsequent
  core halt failed — which made a refused-write look like a broken halt.
- Chip erase no longer aborts when the core cannot be halted. At Level 1 the
  core often can't be, and refusing there would disable the one escape hatch
  precisely when it is needed.

### Measured on real hardware — a full arm/measure/recover cycle
The BAT32G135 was deliberately put into protection Level 1 and brought back.
**Final state: Level 0, code flash md5 `6f37bd86c41a75c19db65cc5824f7199`,
byte-identical to the pre-existing reference dump.** This answers two
questions `07_BAT32G135_FAULTYCAT.md` had left open since it was written:

- ★ **§12 open question #2 — "at protection level N, does the DP still
  answer?" → YES, it is a "soft" target.** At Level 1: `DPIDR=0x0BC11477`
  and `CPUID=0x410CC601` (Cortex-M0+ r0p1) read fine, so the AHB-AP and the
  ARM debug/SCS space stay reachable — but every *flash* access faults
  (`ACK=0x4`), code and data flash alike, option bytes included.
- ★ **Chip erase IS reachable over SWD at Level 1** (undocumented; the
  manual only says it is "permitted"). It cleared OCDEN back to 0xFF and
  restored Level 0. **Programming, by contrast, is refused** at Level 1 —
  `OCDEN 0xC3 → 0x83` failed at the first byte, confirming the §2.3 truth
  table's "lecture et écriture interdites" empirically.

- `SWD RACE` under Level 1 returned **`dp_only` at all six delays**
  (0/100/520/800/1400/2000 µs), as predicted: OCDEN is §8.3's *window A* —
  static, re-applied at every reset, with no temporal window to hit. The
  race targets *window B* (`DBGSTOPCR.SWDIS`, written by firmware at
  runtime). Arming OCDEN therefore does **not** exercise the race, and this
  run should not be read as having tested it.

## [0.10] — 2026-09-01 — SWD physical layer on PIO2, for a genuinely usable `SWD RACE`

### Why

§0bis (added in v0.9's wake) found that the BAT32G135 on this bench was never
protected — the entire `SWD RACE` investigation had been chasing a sampling
artifact of `SWD SPEED 0`, not a real timing race. But `SWD SPEED 0` was also
the *only* bit-bang speed fast enough to fit inside the race window at all
(§8.3's cited 20us-1600us, from the GD32/OFFZONE deck) — every other SPEED
value takes single-digit milliseconds for connect + AHB-AP bring-up + a
2-word read, ~1000x too slow. The contradiction `SWD RACE`'s own guard and
§0bis leave unresolved: the only fast-enough speed is the one that mis-reads.
§9quinquies.3 named the fix directly (translated from the doc's French):
"the real fix would be a reimplementation of SWD in PIO ... this is work
not done here." This entry is that work.

### Added
- `src/swd_phy.pio` + `src/swd_phy.c` + `include/swd_phy.h`: a PIO SWD
  physical layer on **PIO2** (entirely free — PIO0 is 22-32/32 words
  depending on trigger mode, PIO1 is 31/32; see `PIO_ARCHITECTURE.md`'s
  8-SM/64-word claim is wrong for the RP2350, it's 3 blocks x 4 SM x 32
  words). The `swd_phy` program (PIO2 SM0) is a direct, credited port of
  [raspberrypi/debugprobe](https://github.com/raspberrypi/debugprobe)'s
  `probe.pio` (MIT license, full header kept in the file) — its
  command-word protocol (bit count + SWDIO direction + jump target) is
  reused verbatim because the bidirectional SWDIO turnaround it encodes is
  the one genuinely hard part of a PIO SWD phy, and debugprobe already gets
  it right. A second program, `swd_phy_nrst` (PIO2 SM1), is new: a one-shot
  nRST assert/hold-tRSL/release/wait-delay_us sequencer for `SWD RACE`,
  removing the C-side jitter between "reset released" and "first SWCLK
  edge" (previously two `busy_wait_us_32()` calls around a `sleep_us()`
  bit-clock).
- `swd.c`'s bit-bang primitives (`swd_seq_out`/`swd_seq_in`/
  `swd_turnaround`) now dispatch to the PIO phy when active; everything
  above them (DP/AP transactions, `swd_read_mem`, `SWD OPT`, the STM32/LPC
  paths) is unchanged and phy-unaware, by design — the PIO layer replaces
  only the physical bit-clocking, not the ADIv5 protocol logic.
  `swd_seq_out_parity`/`swd_seq_in_parity` were also simplified to emit
  their parity bit via `swd_seq_out(bit,1)`/`swd_seq_in(1)` instead of
  duplicated raw-GPIO code — that raw code would have silently done nothing
  once GP17/18's funcsel moved to PIO (a real, easy-to-miss hazard: a
  PIO-owned pin ignores `gpio_put()`).
- `SWD PHY [BITBANG|PIO [<khz>]]` — get/set the physical layer, default
  `BITBANG` (PIO is opt-in, not a silent behavior change on existing
  workflows). Forces a reconnect (funcsel changes underneath either way).
- `SWD BENCH` — times a fast connect + AHB-AP bring-up + 2-word read (the
  same sequence `SWD RACE` runs per attempt, without touching nRST) at
  whatever phy/speed is active. The before/after measurement tool for this
  change.
- `SWD RACE` now requires `SWD PHY PIO` instead of `SWD SPEED 0` (the old
  guard's premise — that bit-bang is merely *slow* at anything but SPEED
  0 — was only ever half true; SPEED 0 doesn't work at all on this bench).

### Known, deliberate deviations (not bugs)
- `swd_phy_turnaround(true)` (SWDIO FLOAT→DRIVE) sets pindir to output
  *before* the turnaround clock, where the bit-bang path sets it *after*.
  Protocol-safe: ADIv5 defines the turnaround bit's SWDIO value as a
  don't-care on both sides, so the exact instant direction flips within
  that one clock cycle carries no semantic weight. Chosen over a more
  intricate PIO design to match, because there was no hardware available
  this session to validate the fancier version (see below).
- The FIFO-preload race hand-off originally sketched (SM0 pre-armed to
  jump straight into a command sequence on an SM1 IRQ) was dropped in favor
  of a simpler design: SM1 runs the full nRST sequence autonomously and
  signals PIO2 IRQ 4 on completion; the CPU busy-polls that flag (no sleep)
  and then calls the ordinary `swd_connect_ex(true)` path, which issues PIO2
  SM0 commands on demand exactly as it always has. This trades a slightly
  larger (but still sub-microsecond-class) CPU dispatch latency for far
  less PIO code to get right without being able to test on real hardware.

### Validated on real hardware, 2026-09-01

- ★ **Byte-exact dump via the PIO phy.** `scripts/bat32_dump.py --phy pio
  --phy-khz 2500` produced a 64 KB code-flash image with md5
  `6f37bd86c41a75c19db65cc5824f7199` — **identical byte for byte** to the
  bit-bang reference in `assets/bat32_dump_20260831/`, `cmp` clean. This is
  the decisive test: a phy that reproduces that md5 is correct at the
  physical layer. The run printed `Dump complet et verifie` (both flash
  regions re-read and reconciled).
- **`SWD RACE` positive control: 7 SUCCESS / 7 shots** at delays 0, 50, 100,
  520, 800, 1400, 2000 us, every one reporting SP=0x20000E10 PC=0x000001A9
  (the chip's real reset vector). Proves the whole race path — PIO2 SM1 nRST
  sequencer, IRQ hand-off, SM0 connect — runs end to end. As stated below, it
  cannot prove the race would beat a real `SWDIS` write.
- `config_none` regression suite: 96 passed, 0 failed.

### Measured latency, and two corrections this entry had to make to itself

`SWD BENCH` (connect + AHB-AP bring-up + 2-word read — the same sequence
`swd_race_once()` runs), final numbers on the BAT32G135:

| Phy | Total | connect | ahb | read |
|---|---|---|---|---|
| BITBANG `SPEED 4` | 8 755 us | 2 148 | 3 974 | 2 633 |
| BITBANG `SPEED 1` | 2 351 us | 611 | 1 063 | 677 |
| PIO 1000 kHz | 1 159 us | 355 | 415 | 389 |
| PIO 2500 kHz | **531 us** | 155 | 205 | 171 |
| PIO 4000 kHz | 369 us | 116 | 136 | 117 |
| PIO 8000 kHz | **249 us** | 87 | 91 | 71 |

**Gain: 16.5x** at PIO 2500 kHz vs BITBANG `SPEED 4`, 35x at 8 MHz. At
8 MHz the whole race sequence takes **249 us**, comfortably inside the
20 us–1600 us window §8.3 cites.

Two claims this entry made earlier were wrong, in opposite directions, and
both are retracted:

1. **"20x to 80x"** was written from an estimate, not a measurement. It was
   then "corrected" to a measured **7.8x with a ~1.3 ms frequency-independent
   floor** — and *that* was wrong too, because the benchmark itself was
   buggy: `swd_bench()` cleared `ahb_initialized` and never set it back, so
   `swd_read_mem()` took the `!ahb_initialized` branch and re-ran the entire
   bring-up through `swd_init_ahb_ap()` — the `fast=false` variant, with its
   unconditional `sleep_ms(1)` — *inside the timed read phase*. That one
   missing line was ~1.2 ms of the supposed floor. **`swd_race_once()` was
   never affected** (it sets the flag correctly): the race path was always
   this fast; only the measurement lied. Fixed, with a comment at the site.
   The lesson that stuck: instrument the phases *before* forming a theory —
   the per-phase split is now part of `SWD BENCH`'s output and of
   `swd_bench_split_t`.
2. The USB CDC `printf` in `swd_connect_ex()`'s success path was blamed for
   that floor. Removing it from the `fast` path bought only ~7%. It stays
   removed (a blocking stdio call has no business inside a timed critical
   section) but it was not the cause.

### Sequence shortened
- `swd_init_ahb_ap_ex(fast=true)` no longer reads `AP_IDR`. The value was
  read into a local and **never used anywhere in the file**, costing a full
  AP read (posted read + `RDBUFF` DP read, plus a `DP_SELECT` write since
  IDR sits in APBANKSEL 0xF while CSW is in bank 0) on every bring-up.
  Measured: 272 → 249 us total at 8 MHz, `ahb` 115 → 91 us. `fast=false` is
  unchanged, so the STM32/LPC workflows keep the bring-up they were
  validated with.
- Still on the table if a genuinely locked part needs more: the `DPIDR`-only
  oracle (§8.1 — no AHB-AP, no TAR/DRW at all) and dropping the per-word
  `RDBUFF` read in `swd_read_ap()`. Not needed at 249 us.

Re-verified after both optimisations: full dump via PIO still byte-identical
to the reference (`cmp` clean), `SWD RACE` still 4/4 SUCCESS.

★ **This still cannot validate the race itself.** The BAT32G135 on this
bench is Level 0 with `DBGSTOPCR.SWDIS` always 0 (§0bis) — it will connect
at any delay, so `SWD RACE` reporting `SUCCESS` proves the mechanism works
end to end, never that it would beat a real `SWDIS` write on a genuinely
locked part.

## [0.9] — 2026-08-31 — Trim `SWD RACE`'s AHB-AP bring-up

### Changed
- `swd_init_ahb_ap_ex(fast=true)`, used only by `SWD RACE`/`SWD RACE
  SWEEP`: skips the `DP_ABORT` sticky-error clear (`swd_connect_ex(true)`
  already did one right after reading DPIDR) and the power-down request +
  ack-wait loop (the debug domain is guaranteed powered down immediately
  after the hardware reset `swd_race_once()` just performed). Cuts two
  bit-banged DP transactions from the critical path between reset-release
  and AHB-AP being ready. `fast=false` (used by `SWD CONNECTRST` and
  everything else) is unchanged.
- Motivation, and why it no longer holds. This was made during a 2550-shot
  sweep that found 13 `dp_only` hits and 0 getting past `swd_init_ahb_ap_ex`,
  read at the time as "the race window closes before AHB-AP finishes".
  ★ **That reading was wrong and is retracted.** Those hits were a sampling
  artifact of running at `SWD SPEED 0`, which mis-samples on that bench: the
  DPIDR they returned (`0x178028EF`) is a canonical ARM DPIDR shifted left by
  one bit. At `SWD SPEED 4` the same target connects first try, and its
  `DBGSTOPCR.SWDIS` is 0 — there was no race to win. See §0bis of
  `TPLink_Tapo/07_BAT32G135_FAULTYCAT.md`.
- The change itself is kept: skipping work the hardware reset already
  guarantees is still correct, and it measurably doubled the hit rate under
  the (bogus) conditions it was measured in. But it is now **unexercised** —
  no current workflow uses `fast=true`, since the BAT32 path is a plain
  `SWD CONNECT` at reduced speed. Anyone reviving `SWD RACE` for a genuinely
  locked part should re-validate it, including that dropping the `DP_ABORT`
  clear (which `fast=false` still performs) is safe on their target.

## [0.8] — 2026-08-30 — SWD reset-release race + read-only BAT32G135 support

### Added
- `SWD RACE [<delay_us>]` and `SWD RACE SWEEP <start_us> <end_us> <step_us>
  [SHOTS <n>]`: a reset-release timing race, distinct from `SWD
  CONNECTRST`. Asserts/releases nRST, waits exactly `delay_us` (no
  clamping, no compensation), then races to connect + bring up the AHB-AP
  + read the reset vector before target firmware can lock SWD (e.g. by
  writing a runtime SWD-disable bit). `SWD CONNECTRST` connects *before*
  releasing reset and needs the SW-DP to answer while held in reset —
  `SWD RACE` is for the opposite case, where it does not. Requires `SWD
  SPEED 0` first. The SWEEP variant is bounded (max 200000 delay points x
  1000 shots), aborts on any pending input, and stops immediately on the
  first SUCCESS with the target left powered and connected (Level 0 does
  not survive the next reset).
- `swd_connect_ex(bool fast)` / internal `swd_init_ahb_ap_ex(bool fast)`:
  a fast connect + AHB-AP bring-up path used by `SWD RACE`, skipping the
  pin-settle delay, the ADIv5.2 dormant-state fallback, and the
  unconditional 1ms wait before the first debug-power-up-ack check —
  together the largest fixed costs on the reset-release critical path.
  `swd_connect()` is unchanged (`swd_connect_ex(false)`).
- `TARGET BAT32` — read-only support for the Cmsemicon BAT32G135
  (Cortex-M0+): `SWD READ FLASH|SRAM` now resolve to its memory map, and
  `SWD OPT` decodes OCDEN/OCDM/BTEN (both option-byte clusters, including
  the boot-swap mirror) and `DBGSTOPCR.SWDIS`, printing the deduced
  protection level (Level 0/1/2 per the official truth table). No flash
  write/erase path is implemented — `SWD RDP` and `SWD FLASH ERASE`
  explicitly refuse a BAT32 target rather than silently doing nothing.

### Fixed
- `SET PAUSE/WIDTH/GAP/COUNT/VMIN <value>` now validates the value via
  `parse_u32()` and returns an explicit `ERROR:` on a malformed or
  out-of-range token, instead of silently taking whatever `atoi()`
  returned (garbage input, e.g. `SET PAUSE abc`, previously became 0).
- `DBGMCU_CR` (0xE0042004, STM32-only) is no longer written unconditionally
  by `SWD CONNECTRST`, and `DBG_IDCODE` (0xE0042000, also STM32-only) is
  no longer read unconditionally by `swd_detect()` / `SWD IDCODE` — both
  now check `target_is_stm32()` first (skipped, or read as "unknown", for
  a target explicitly set to something else, e.g. BAT32G135).

## [0.7] — 2026-06-08 — ChipSHOUTER command fixes + hardening

### Fixed
- `CS FAULTS` now sends `get fault` (was `get faults_current`, which the
  ChipSHOUTER console rejected with "Command Not Found").
- `CS HVOUT` now sends `get voltage` (was `help`, which dumped the command list);
  reports the capacitor-bank charge voltage — set value plus the measured HV when armed.
- `CS TRIGGER HW` now also sends `set hwtrig_term 0` (high-impedance). The 50 Ω
  default (restored by every `CS RESET`) held the trigger input below its 2 V
  threshold for the Pico's 3.3 V GP2 GPIO, so the CS armed/charged but never fired.
- `CS TRIGGER SW` sends `set hwtrig_term 1` (was the invalid `True`) and no longer
  sends the invalid `set emode True`.
- `glitch_heatmap.py`: on `cs_error`, retry cheaply and only `CS RESET` + cool down
  once faults **persist** (was resetting on every transient, which stalled scans /
  thrashed; combined with a CS that trips without the literal "fault" string).

### Host tooling (`glitch_heatmap.py`)
- New `--drop N` (default 1): quickmap drops the voltage only after **N consecutive
  non-normals**, instead of on the first hit — so a single noise hit no longer moves
  the voltage. A normal breaks the hit streak (and vice-versa).
- New `--pause N` (default 0): glitch PAUSE in 150 MHz cycles is now a CLI arg
  (was hardcoded to 5000). Note: the LPC1114 success in `SUCCESS_FOUND.md` was at
  PAUSE 0, but this target's CRP-check window opens later — effects appear at
  PAUSE ≈ 3000–5000, nothing at 0/1000.
- `--shots` is now a **per-voltage** cap in quickmap, not a per-cell cap: the voltage
  descent always continues to the floor (`CS_VOLTAGE_MIN`); the cap only stops a
  non-converging single voltage from looping forever.

### Added
- `CS VOLTAGE` / `CS PULSE` argument guardrails: range-validate (150–500 V /
  80–1000 ns) and reject garbage/out-of-range **before** sending to the CS.
- CS replies are scanned for "Command Not Found" and surfaced as an `ERROR`, so a
  bad firmware command string can no longer masquerade as success.
- `config_none` regression tests for the CS guardrail error paths (`TestCsCommands`).

## [0.6] — 2026-06-07 — Crowbar EXTERNAL power mode + cleanup (PR #8)

### Added
- EXTERNAL crowbar power mode: `TARGET POWER EXTERNAL` re-tasks the GP10/11/12
  group — GP10 = supply enable, GP11 = PIO-driven crowbar gate (AHIGH/ALOW idle
  polarity), GP12 = spare. The gate emits the same waveform as GP2 via a 2nd
  pulse_generator SM on PIO0 SM3, with a soft-disarm so multi-pulse trains finish.
- Auto-power-on at the `TARGET SYNC` / `SWD CONNECT` choke points, with a
  cold-target settle delay before the first transaction.
- Power-test safety gating (`--config=power-int` / `--config=power-ext`) and a
  reboot-based boot-power-default regression test.

### Changed
- `TARGET POWER MODE <X>` → `TARGET POWER <X>` (the `MODE` keyword was dropped).
- Boot power default flipped to OFF (de-energized) in both modes.
- ADC channels renamed to the official 0-based numbering: `ADC 0` = GP26,
  `ADC 1` = GP27 (the old `ADC 2` now errors).

### Removed
- The never-implemented PLATFORM command and its abstraction
  (`platform.c`/`.pio`/`.h`, `PLATFORM_GUIDE.md`).

## [0.5] and earlier

Predates this changelog. Highlights from git history: the EXTERNAL power mode with
the PIO crowbar gate on GP11, the `pins` GPIO-assignment skill, shared host-script
tooling (CLI colors + Rigol scope helpers), the ADC-gated `VMIN` glitch primitive,
and the LPC/STM32 bootloader + bit-banged SWD/JTAG support. Initial public commit
was v0.3. See `git log` for detail.
