/*
 * PIO SWD physical layer for raiden-pico -- see swd_phy.h and swd_phy.pio
 * for the design notes. This file only wraps the generated PIO program;
 * all ADIv5 protocol logic lives in swd.c and is unaware of this file.
 */

#include "swd_phy.h"
#include "swd_phy.pio.h"
#include "hardware/pio.h"
#include "hardware/gpio.h"
#include "hardware/clocks.h"
#include "pico/time.h"

static PIO const PHY_PIO = pio2;
static const uint SM_PHY  = 0;
static const uint SM_NRST = 1;

static bool phy_prog_loaded = false;
static uint phy_prog_offset = 0;
static bool phy_active = false;
static uint32_t phy_khz_cur = 0;

static bool nrst_prog_loaded = false;
static uint nrst_prog_offset = 0;

// Command word for swd_phy's get_next_cmd dispatcher:
// | 13:9 addr (jump target) | 8 dir (SWDIO pindir) | 7:0 count-1 |
static inline uint32_t phy_cmd(uint addr, bool dir, size_t bits) {
    return ((addr & 0x1Fu) << 9) | ((dir ? 1u : 0u) << 8) |
           (((uint32_t)bits - 1u) & 0xFFu);
}

// Charge les DEUX programmes PIO tout de suite, a l'initialisation.
//
// Sans cela, swd_phy_nrst_program n'est charge qu'au PREMIER `SWD RACE`, par
// swd_phy_nrst_race(), c'est-a-dire depuis l'interieur de la section
// chronometree. Deux consequences, toutes deux mauvaises :
//   - pio_add_program() fait un hard_assert s'il ne reste plus assez de mots
//     dans PIO2 : la panique survient a la premiere commande RACE, pas au
//     boot -- un mode de defaillance differe et tres deroutant ;
//   - l'espace instruction de PIO2 n'est pas reserve, alors que le driver
//     CYW43 (Pico 2 W) vient y chercher de la place a l'init.
// Les gardes `if (!*_prog_loaded)` existants rendent le reste du fichier
// inchange : les charges paresseuses deviennent simplement des no-op.
void swd_phy_preload_programs(void) {
    if (!phy_prog_loaded) {
        phy_prog_offset = pio_add_program(PHY_PIO, &swd_phy_program);
        phy_prog_loaded = true;
    }
    if (!nrst_prog_loaded) {
        nrst_prog_offset = pio_add_program(PHY_PIO, &swd_phy_nrst_program);
        nrst_prog_loaded = true;
    }
}

bool swd_phy_init(uint32_t pin_swclk, uint32_t pin_swdio, uint32_t khz) {
    if (khz == 0)
        return false;

    if (phy_active)
        pio_sm_set_enabled(PHY_PIO, SM_PHY, false);

    if (!phy_prog_loaded) {
        phy_prog_offset = pio_add_program(PHY_PIO, &swd_phy_program);
        phy_prog_loaded = true;
    }

    swd_phy_gpio_claim(pin_swclk, pin_swdio);

    // SWCLK period is 4 SM execution cycles (see swd_phy.pio's header
    // comment) -- clkdiv such that clk_sys/clkdiv/4 == khz*1000.
    float clkdiv = (float)clock_get_hz(clk_sys) / (4.0f * (float)khz * 1000.0f);
    if (clkdiv < 1.0f)
        clkdiv = 1.0f;  // clkdiv can't go below 1; clamps requested khz to clk_sys/4

    pio_sm_clear_fifos(PHY_PIO, SM_PHY);
    swd_phy_program_init(PHY_PIO, SM_PHY, phy_prog_offset, pin_swclk, pin_swdio, clkdiv);
    pio_sm_restart(PHY_PIO, SM_PHY);
    // pio_sm_init() leaves PC at the program's first instruction
    // (write_cmd), which starts with a `pull` that expects a DATA word.
    // The real idle entry point is get_next_cmd, which pulls a COMMAND
    // word -- force PC there before the first real transfer.
    pio_sm_exec(PHY_PIO, SM_PHY,
                pio_encode_jmp(phy_prog_offset + swd_phy_offset_get_next_cmd));
    pio_sm_set_enabled(PHY_PIO, SM_PHY, true);

    phy_active = true;
    phy_khz_cur = khz;
    return true;
}

void swd_phy_deinit(uint32_t pin_swclk, uint32_t pin_swdio) {
    if (!phy_active)
        return;
    pio_sm_set_enabled(PHY_PIO, SM_PHY, false);
    swd_phy_gpio_release(pin_swclk, pin_swdio);
    phy_active = false;
    phy_khz_cur = 0;
}

bool swd_phy_is_active(void) {
    return phy_active;
}

uint32_t swd_phy_get_khz(void) {
    return phy_khz_cur;
}

// phy_cmd()'s addr argument is an ABSOLUTE PIO2 instruction address, but
// swd_phy_offset_write_cmd/swd_phy_offset_read_cmd are pioasm-generated
// constants relative to the program's own start (both 0 today, since
// swd_phy is the only program ever loaded ahead of them). They only happen
// to line up because pio_add_program() placed swd_phy at offset 0 -- true
// today (PIO2 starts empty and swd_phy_init() always loads swd_phy before
// anything else can touch PIO2), but not guaranteed by the type system.
// Every phy_cmd() call below adds phy_prog_offset explicitly so this stays
// correct even if that ever stops being true.

void swd_phy_out(uint32_t data, size_t bits) {
    if (bits == 0)
        return;
    pio_sm_put_blocking(PHY_PIO, SM_PHY,
                         phy_cmd(phy_prog_offset + swd_phy_offset_write_cmd, true, bits));
    pio_sm_put_blocking(PHY_PIO, SM_PHY, data);
}

uint32_t swd_phy_in(size_t bits) {
    if (bits == 0)
        return 0;
    pio_sm_put_blocking(PHY_PIO, SM_PHY,
                         phy_cmd(phy_prog_offset + swd_phy_offset_read_cmd, false, bits));
    uint32_t raw = pio_sm_get_blocking(PHY_PIO, SM_PHY);
    // ISR shifts right, MSB-loaded each `in` -- the block of `bits` sampled
    // bits ends up top-justified; >> (32-bits) moves it down to the same
    // LSB-first packing swd_seq_in() produces.
    return raw >> (32u - (uint32_t)bits);
}

void swd_phy_turnaround(bool drive) {
    if (drive) {
        // FLOAT -> DRIVE: write_cmd sets pindir to output, then clocks once
        // with a don't-care bit (ADIv5 doesn't define the turnaround bit's
        // value). See the ordering note in swd_phy.h.
        pio_sm_put_blocking(PHY_PIO, SM_PHY,
                             phy_cmd(phy_prog_offset + swd_phy_offset_write_cmd, true, 1));
        pio_sm_put_blocking(PHY_PIO, SM_PHY, 0);
    } else {
        // DRIVE -> FLOAT: read_cmd sets pindir to input, then clocks once,
        // sampled bit discarded. This order (release before clock) matches
        // the bit-bang path exactly.
        pio_sm_put_blocking(PHY_PIO, SM_PHY,
                             phy_cmd(phy_prog_offset + swd_phy_offset_read_cmd, false, 1));
        (void)pio_sm_get_blocking(PHY_PIO, SM_PHY);
    }
}

bool swd_phy_nrst_race(uint32_t pin_nrst, uint32_t delay_us) {
    if (!phy_active)
        return false;  // PIO2 must already be claimed by swd_phy_init()

    if (!nrst_prog_loaded) {
        nrst_prog_offset = pio_add_program(PHY_PIO, &swd_phy_nrst_program);
        nrst_prog_loaded = true;
    }

    pio_gpio_init(PHY_PIO, pin_nrst);

    // 2 SM cycles = 1 microsecond -- see swd_phy_nrst's header comment.
    float clkdiv = (float)clock_get_hz(clk_sys) / 2000000.0f;
    if (clkdiv < 1.0f)
        clkdiv = 1.0f;
    swd_phy_nrst_program_init(PHY_PIO, SM_NRST, nrst_prog_offset, pin_nrst, clkdiv);

    pio_sm_clear_fifos(PHY_PIO, SM_NRST);
    pio_interrupt_clear(PHY_PIO, 4);
    pio_sm_restart(PHY_PIO, SM_NRST);
    pio_sm_exec(PHY_PIO, SM_NRST,
                pio_encode_jmp(nrst_prog_offset + swd_phy_nrst_offset_nrst_start));
    pio_sm_set_enabled(PHY_PIO, SM_NRST, true);

    // The program asserts nRST and holds tRSL (>=10us) entirely on its own
    // before it ever reaches `pull` -- pushing delay_us any time before or
    // during that hold is fine, the TX FIFO buffers it regardless of when
    // the SM actually executes the pull.
    pio_sm_put_blocking(PHY_PIO, SM_NRST, delay_us);

    // Bounded poll, no sleep -- this loop IS the timed critical section, a
    // sleep here would reintroduce exactly the jitter this function exists
    // to remove. Timeout is generous on purpose: a wiring fault (e.g. no
    // pull-up on nRST) must not hang the CLI forever.
    uint64_t deadline_us = to_us_since_boot(get_absolute_time()) +
                            (uint64_t)delay_us + 10000u + 50u;
    bool ok = false;
    while (to_us_since_boot(get_absolute_time()) < deadline_us) {
        if (pio_interrupt_get(PHY_PIO, 4)) {
            ok = true;
            break;
        }
    }

    pio_interrupt_clear(PHY_PIO, 4);
    pio_sm_set_enabled(PHY_PIO, SM_NRST, false);

    // Hand nRST back to plain SIO input -- matches the electrical state PIO
    // already left it in (released, hi-Z, external pull-up holds it high)
    // and restores every other nRST call site (SWD RESET, connect-under-
    // reset, ...) to working via gpio_put()/gpio_set_dir() as before.
    gpio_init(pin_nrst);

    return ok;
}
