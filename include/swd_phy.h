/*
 * PIO SWD physical layer for raiden-pico (PIO2 -- see swd_phy.pio for why).
 *
 * This is deliberately a thin layer: three primitives that emit the same
 * number of SWCLK edges as the bit-banged equivalents in swd.c, plus the
 * one-shot nRST sequencer used by SWD RACE. All ADIv5 protocol logic
 * (requests, ACK handling, parity, WAIT retries, AP/DP semantics) stays in
 * swd.c and is unaware of which physical layer is active.
 *
 * Not thread/ISR-safe -- called only from the single-threaded CLI command
 * loop, same as the rest of swd.c.
 */

#ifndef SWD_PHY_H
#define SWD_PHY_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

// Bring up PIO2 SM0 running swd_phy, on the given pins, at the given SWCLK
// frequency (kHz). Claims pin funcsel for pin_swclk/pin_swdio (PIO2 -- does
// not touch PIO0/1). Safe to call again to change frequency (tears down and
// re-inits). Returns false if khz is 0.
// Charge les programmes PIO de la couche physique sans rien activer. A
// appeler une fois au demarrage : cela sort le pio_add_program du programme
// nRST du chemin chronometre de `SWD RACE`, et reserve l'espace instruction
// de PIO2 avant qu'un autre pilote (CYW43) ne vienne le convoiter.
void swd_phy_preload_programs(void);

bool swd_phy_init(uint32_t pin_swclk, uint32_t pin_swdio, uint32_t khz);

// Hand pin_swclk/pin_swdio back to plain SIO GPIO (input, no pulls) and
// release PIO2 SM0. Safe to call when not initialized (no-op).
void swd_phy_deinit(uint32_t pin_swclk, uint32_t pin_swdio);

bool swd_phy_is_active(void);
uint32_t swd_phy_get_khz(void);

// Drive `bits` bits of `data` out on SWDIO, LSB first, side-setting SWCLK --
// same bit ordering, same "ends with SWCLK low" postcondition as the
// bit-banged swd_seq_out(). Caller (swd.c) is responsible for SWDIO already
// being in the DRIVE direction (via swd_phy_turnaround()) -- this function
// does not change direction itself for a plain data transfer.
void swd_phy_out(uint32_t data, size_t bits);

// Sample `bits` bits from SWDIO, LSB first -- same postconditions as the
// bit-banged swd_seq_in(). Caller is responsible for SWDIO already being in
// the FLOAT direction.
uint32_t swd_phy_in(size_t bits);

// Exactly one SWCLK pulse, changing SWDIO's direction to `drive` (true =
// becomes an output, false = becomes an input). Mirrors the bit-bang
// swd_turnaround()'s *transition* branches only -- the no-op-if-unchanged
// check and swdio_dir bookkeeping stay in swd.c's swd_turnaround(), which
// calls this only when it is about to actually emit a clock.
//
// Direction changes relative to the clock edge intentionally do not
// reproduce the bit-bang path's before/after asymmetry for the DRIVE case
// (debugprobe's single-command protocol sets direction and clocks
// atomically). This is protocol-safe: ADIv5 defines the turnaround bit's
// SWDIO value as a don't-care on both sides, so the exact instant direction
// flips within that one clock cycle carries no semantic weight. See the
// v0.10 CHANGELOG entry.
void swd_phy_turnaround(bool drive);

// --- SWD RACE nRST sequencer (PIO2 SM1, swd_phy_nrst program) ---

// One-shot, PIO-timed reset-release race pulse: takes over pin_nrst from
// SIO for the duration of the call (handed back to SIO before returning),
// asserts it, holds >= 10us (tRSL, [DS] Sec 6.6), releases to hi-Z (relies
// on the target's own pull-up, exactly like the bit-banged
// swd_nrst_release()), then busy-waits exactly delay_us -- no clamp, no
// rounding, 0 is legal -- before returning. Requires swd_phy_init() to have
// already claimed PIO2 (uses the same instance, a different SM).
//
// Bounded internally: gives up and returns after a generous timeout
// (delay_us plus a fixed margin) if the PIO sequence never signals
// completion, rather than hanging the CLI forever on a wiring fault.
// Returns false on that timeout (nRST is still handed back to SIO either
// way); true on the normal path.
bool swd_phy_nrst_race(uint32_t pin_nrst, uint32_t delay_us);

#endif // SWD_PHY_H
