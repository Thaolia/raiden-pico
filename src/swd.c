/*
 * SWD (Serial Wire Debug) implementation for Raiden-Pico
 *
 * Bit-banged SWD interface for target debugging.
 * Based on ARM Debug Interface v5 Architecture Specification.
 */

#include "swd.h"
#include "net_cli.h"
#include "swd_phy.h"
#include "bat32_target.h"
#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/timer.h"
#include <stdio.h>

// Clock half-period in microseconds (0 = max speed, 1 = ~500kHz, 2 = ~250kHz)
// Default 1: max speed=0 is too fast for STM32F1 @ 8MHz HSI (AP reads fail)
static uint32_t clk_delay_us = 1;

// Physical layer selection -- see swd_set_phy_mode() / swd_phy.h. Default
// bit-bang: PIO mode is an explicit opt-in (SWD PHY PIO), not a silent
// change in boot behavior.
static swd_phy_mode_t phy_mode = SWD_PHY_BITBANG;
static uint32_t phy_khz_last = 0;

static bool initialized = false;
static bool connected = false;
static bool ahb_initialized = false;
static uint8_t last_ack = 0;
static uint32_t current_select = 0xFFFFFFFF;

// Track SWDIO direction to minimize turnarounds
typedef enum {
    SWDIO_FLOAT = 0,  // Input
    SWDIO_DRIVE = 1   // Output
} swdio_dir_t;
static swdio_dir_t swdio_dir = SWDIO_FLOAT;

// JTAG-to-SWD switch sequence
#define JTAG_TO_SWD_SEQUENCE 0xE79E

// ADIv5.2 Selection Alert sequence (128 bits, LSB first)
#define SELECTION_ALERT_0 0x6209F392U
#define SELECTION_ALERT_1 0x86852D95U
#define SELECTION_ALERT_2 0xE3DDAFE9U
#define SELECTION_ALERT_3 0x19BC0EA2U

// Activation code for ARM SWD DP
#define ACTIVATION_CODE_SWD 0x1AU

// --- Low-level bit operations ---

static inline void clk_delay(void) {
    if (clk_delay_us)
        sleep_us(clk_delay_us);
}

void swd_set_speed(uint32_t delay_us) {
    clk_delay_us = delay_us;
}

uint32_t swd_get_speed(void) {
    return clk_delay_us;
}

bool swd_set_phy_mode(swd_phy_mode_t mode, uint32_t khz_if_pio) {
    if (mode == SWD_PHY_PIO) {
        uint32_t khz = khz_if_pio ? khz_if_pio : phy_khz_last;
        if (khz == 0)
            return false;  // never configured, and none given now
        if (connected)
            swd_deinit();  // pins' funcsel changes underneath either way
        phy_mode = SWD_PHY_PIO;
        phy_khz_last = khz;
        // Actual pio_add_program()/pio_sm_init() happens lazily in
        // swd_connect_ex() (mirrors the bit-bang path, which also only
        // touches pins there) -- this call just records the mode + freq.
    } else {
        if (connected)
            swd_deinit();
        if (phy_mode == SWD_PHY_PIO)
            swd_phy_deinit(SWD_SWCLK_PIN, SWD_SWDIO_PIN);
        phy_mode = SWD_PHY_BITBANG;
    }
    return true;
}

swd_phy_mode_t swd_get_phy_mode(void) {
    return phy_mode;
}

uint32_t swd_get_phy_khz(void) {
    return phy_khz_last;
}

static inline void swclk_set(void) {
    gpio_put(SWD_SWCLK_PIN, 1);
}

static inline void swclk_clr(void) {
    gpio_put(SWD_SWCLK_PIN, 0);
}

static inline void swdio_set(void) {
    gpio_put(SWD_SWDIO_PIN, 1);
}

static inline void swdio_clr(void) {
    gpio_put(SWD_SWDIO_PIN, 0);
}

static inline bool swdio_get(void) {
    return gpio_get(SWD_SWDIO_PIN);
}

static inline void swdio_out(void) {
    gpio_set_dir(SWD_SWDIO_PIN, GPIO_OUT);
}

static inline void swdio_in(void) {
    gpio_set_dir(SWD_SWDIO_PIN, GPIO_IN);
}

// Turnaround - exact copy of BMP swdptap_turnaround for the bit-bang path.
// Does NOT assume a specific SWCLK state on entry. The no-op-if-unchanged
// check and swdio_dir bookkeeping apply to BOTH physical layers -- this is
// the only place that decides whether a clock is emitted at all; it calls
// out to swd_phy_turnaround() only on the branch where it actually is.
static void swd_turnaround(swdio_dir_t dir) {
    if (dir == swdio_dir)
        return;
    swdio_dir = dir;

    if (phy_mode == SWD_PHY_PIO) {
        swd_phy_turnaround(dir == SWDIO_DRIVE);
        return;
    }

    if (dir == SWDIO_FLOAT) {
        // BMP: release SWDIO, SWCLK untouched
        swdio_in();
    } else {
        // BMP: ensure SWCLK LOW before turnaround clock
        // (handles both HIGH from parity read and LOW from seq_out)
        swclk_clr();
    }

    clk_delay();
    swclk_set();
    clk_delay();

    if (dir == SWDIO_DRIVE) {
        swclk_clr();
        swdio_out();
    }
}

// Output bits LSB first (matches BMP swdptap_seq_out_clk_delay)
// Ends with SWCLK LOW (trailing clr, same as BMP) in the bit-bang path;
// swd_phy_out() reproduces the same LSB-first / SWCLK-low postcondition.
static void swd_seq_out(uint32_t data, size_t bits) {
    swd_turnaround(SWDIO_DRIVE);
    if (phy_mode == SWD_PHY_PIO) {
        swd_phy_out(data, bits);
        return;
    }
    for (size_t i = 0; i < bits; i++) {
        swclk_clr();
        gpio_put(SWD_SWDIO_PIN, data & 1);
        clk_delay();
        swclk_set();
        clk_delay();
        data >>= 1;
    }
    swclk_clr();
}

// Input bits LSB first (matches BMP swdptap_seq_in_clk_delay)
// Sample immediately after falling edge (same as BMP) in the bit-bang path;
// swd_phy_in() reproduces the same LSB-first sample-on-falling-edge result.
static uint32_t swd_seq_in(size_t bits) {
    swd_turnaround(SWDIO_FLOAT);
    if (phy_mode == SWD_PHY_PIO)
        return swd_phy_in(bits);
    uint32_t data = 0;
    for (size_t i = 0; i < bits; i++) {
        swclk_clr();
        if (swdio_get())
            data |= (1U << i);
        clk_delay();
        swclk_set();
        clk_delay();
    }
    swclk_clr();
    return data;
}

// Calculate odd parity
static bool calc_parity(uint32_t data) {
    data ^= data >> 16;
    data ^= data >> 8;
    data ^= data >> 4;
    data ^= data >> 2;
    data ^= data >> 1;
    return data & 1;
}

// Output 32 bits with parity (matches BMP swdptap_seq_out_parity).
// The parity bit is emitted via swd_seq_out(bit, 1) rather than duplicated
// raw GPIO code -- for bits=1 that call reduces to exactly the same
// CLR/write/delay/SET/delay/CLR pattern this used to do inline (the
// leading CLR is redundant, SWCLK is already low from seq_out's trailing
// CLR, matching BMP), and it's what keeps this function correct in
// SWD_PHY_PIO instead of poking SIO registers a PIO-owned pin ignores.
static void swd_seq_out_parity(uint32_t data) {
    swd_seq_out(data, 32);
    swd_seq_out(calc_parity(data) ? 1u : 0u, 1);
}

// Input 32 bits with parity check (matches BMP swdptap_seq_in_parity).
// The parity bit is read via swd_seq_in(1) for the same reason as above:
// for bits=1 it samples at the identical point (immediately after the
// falling edge, before the delay) as the raw code this replaces.
// Ends SWCLK LOW, then calls turnaround(DRIVE).
static bool swd_seq_in_parity(uint32_t *data) {
    *data = swd_seq_in(32);
    bool parity_bit = (swd_seq_in(1) & 1u) != 0;
    swd_turnaround(SWDIO_DRIVE);
    return calc_parity(*data) == parity_bit;
}

// Build request packet (from Black Magic Probe)
static uint8_t make_request(bool APnDP, bool RnW, uint8_t addr) {
    uint8_t request = 0x81;  // Start bit + Park bit

    if (APnDP)
        request ^= 0x22;
    if (RnW)
        request ^= 0x24;

    addr &= 0xC;
    request |= (addr << 1) & 0x18;
    if (addr == 4 || addr == 8)
        request ^= 0x20;

    return request;
}

// Line reset sequence (56+ cycles with SWDIO HIGH, no trailing idle)
static void swd_line_reset(void) {
    swd_seq_out(0xFFFFFFFF, 32);  // 32 HIGH
    swd_seq_out(0x00FFFFFF, 24);  // 24 HIGH (total 56)
}

// Idle cycles (SWDIO LOW) - call after line reset before first transaction
static void swd_idle(int cycles) {
    swd_seq_out(0, cycles);
}

// --- Public API ---

void swd_init(void) {
    gpio_init(SWD_SWCLK_PIN);
    gpio_set_dir(SWD_SWCLK_PIN, GPIO_OUT);
    gpio_put(SWD_SWCLK_PIN, 0);

    gpio_init(SWD_SWDIO_PIN);
    gpio_set_dir(SWD_SWDIO_PIN, GPIO_OUT);
    gpio_put(SWD_SWDIO_PIN, 1);

    swdio_dir = SWDIO_DRIVE;
    initialized = true;
    current_select = 0xFFFFFFFF;
}

// Forward declarations for swd_deinit
static bool mem_write32(uint32_t addr, uint32_t val);
static bool mem_read32(uint32_t addr, uint32_t *val);
static bool swd_init_ahb_ap(void);

void swd_deinit(void) {
    if (!initialized) return;

    if (connected) {
        // Clear vector catch so nRST doesn't halt at reset vector
        mem_write32(0xE000EDFC, 0);  // DEMCR = 0

        // Resume target (clear C_HALT, keep C_DEBUGEN)
        mem_write32(0xE000EDF0, 0xA05F0001);  // DHCSR = DBGKEY | C_DEBUGEN

        // Disable debug entirely
        mem_write32(0xE000EDF0, 0xA05F0000);  // DHCSR = DBGKEY only (C_DEBUGEN cleared)

        // Clear sticky errors
        swd_write_dp(DP_ABORT, 0x1E);

        // Power down debug domain
        swd_write_dp(DP_CTRL_STAT, 0);

        // Wait for power-down acknowledge
        for (int i = 0; i < 50; i++) {
            uint32_t stat;
            if (swd_read_dp(DP_CTRL_STAT, &stat) && !(stat & 0xA0000000))
                break;
            sleep_ms(1);
        }
    }

    if (phy_mode == SWD_PHY_PIO) {
        swd_phy_deinit(SWD_SWCLK_PIN, SWD_SWDIO_PIN);  // funcsel back to SIO input
    } else {
        gpio_set_dir(SWD_SWCLK_PIN, GPIO_IN);
        gpio_set_dir(SWD_SWDIO_PIN, GPIO_IN);
    }
    initialized = false;
    connected = false;
}

bool swd_connect_ex(bool fast) {
    if (!initialized)
        swd_init();

    connected = false;
    ahb_initialized = false;
    current_select = 0xFFFFFFFF;

    if (phy_mode == SWD_PHY_PIO) {
        // Re-claim PIO2 SM0 at the last-configured frequency (in case
        // another subsystem changed pin function -- same rationale as the
        // bit-bang branch below). swd_phy_init() is idempotent.
        swd_phy_init(SWD_SWCLK_PIN, SWD_SWDIO_PIN, phy_khz_last);
    } else {
        // Force full pin init (in case another subsystem changed pin function)
        gpio_init(SWD_SWCLK_PIN);
        gpio_set_dir(SWD_SWCLK_PIN, GPIO_OUT);
        gpio_put(SWD_SWCLK_PIN, 0);
        gpio_init(SWD_SWDIO_PIN);
        gpio_set_dir(SWD_SWDIO_PIN, GPIO_OUT);
        gpio_put(SWD_SWDIO_PIN, 1);
    }
    swdio_dir = SWDIO_DRIVE;
    // fast (race path): skip the pin-settle delay — it's the single biggest
    // fixed cost on the reset-release -> first-SWCLK-edge critical path.
    if (!fast)
        sleep_ms(1);  // Let pins settle

    // === Method 1: Legacy JTAG-to-SWD ===
    swd_line_reset();
    swd_seq_out(JTAG_TO_SWD_SEQUENCE, 16);
    swd_line_reset();
    swd_idle(4);

    uint32_t dpidr;
    if (swd_read_dp(DP_DPIDR, &dpidr)) {
        swd_write_dp(DP_ABORT, 0x1E);
        // Not on the fast path: this is a USB CDC printf, and stdio over CDC
        // blocks for on the order of a millisecond. Called from
        // swd_race_once(), it lands INSIDE the timed critical section and
        // dwarfs the race window it is trying to hit (measured 2026-09-01:
        // it is the bulk of SWD BENCH's frequency-independent floor -- at
        // PIO 8 MHz the whole sequence took 1521us, of which the actual SWD
        // traffic is a small fraction). The caller prints DPIDR itself.
        if (!fast)
            printf("[SWD] Connected, DPIDR=0x%08X\r\n", (unsigned)dpidr);
        connected = true;
        return true;
    }

    // fast (race path): don't try the ADIv5.2 dormant-state fallback — it
    // doubles the connect duration and a dormant-state target is out of
    // scope for a reset-release race (the target just came out of reset).
    if (fast) {
        printf("[SWD] Connect failed (fast), ACK=0x%X\r\n", last_ack);
        return false;
    }

    // === Method 2: ADIv5.2 Dormant-to-SWD ===
    gpio_set_dir(SWD_SWDIO_PIN, GPIO_OUT);
    gpio_put(SWD_SWDIO_PIN, 1);
    swdio_dir = SWDIO_DRIVE;

    swd_seq_out(0xFF, 8);
    swd_seq_out(SELECTION_ALERT_0, 32);
    swd_seq_out(SELECTION_ALERT_1, 32);
    swd_seq_out(SELECTION_ALERT_2, 32);
    swd_seq_out(SELECTION_ALERT_3, 32);
    swd_seq_out(0, 4);
    swd_seq_out(ACTIVATION_CODE_SWD, 8);
    swd_line_reset();
    swd_idle(4);

    if (swd_read_dp(DP_DPIDR, &dpidr)) {
        swd_write_dp(DP_ABORT, 0x1E);
        printf("[SWD] Connected (dormant), DPIDR=0x%08X\r\n", (unsigned)dpidr);
        connected = true;
        return true;
    }

    printf("[SWD] Connect failed, ACK=0x%X\r\n", last_ack);
    return false;
}

bool swd_connect(void) {
    return swd_connect_ex(false);
}

bool swd_is_connected(void) {
    return connected;
}

bool swd_ensure_connected(void) {
    if (connected)
        return true;
    // Energise the target before connecting — covers the power-off boot default,
    // so SWD READ/WRITE/HALT (and breakpoint helpers) work without a host POWER ON.
    extern void target_power_ensure_on(void);
    target_power_ensure_on();
    return swd_connect();
}

// (Forward declarations moved above swd_deinit)

bool swd_connect_under_reset(void) {
    // BMP-style connect-under-reset with VC_CORERESET vector catch.
    // This halts the core at the reset vector BEFORE any firmware runs,
    // which is critical when the target has a watchdog that would
    // otherwise reset the core before we can halt it.

    // Step 1: Hold target in reset
    swd_nrst_assert();
    sleep_ms(10);

    // Step 2: Connect SWD while target is held in reset
    if (!swd_connect()) {
        printf("[SWD] CUR: connect failed\r\n");
        swd_nrst_release();
        return false;
    }

    // Step 3: Always re-init AHB-AP after reset (AP state may be stale)
    ahb_initialized = false;
    if (!swd_init_ahb_ap()) {
        printf("[SWD] CUR: AHB-AP init failed\r\n");
        swd_nrst_release();
        return false;
    }
    ahb_initialized = true;

    // Step 4: Enable debug (C_DEBUGEN) and request halt (C_HALT)
    mem_write32(DHCSR, DBGKEY | 0x3);

    // Step 5: Set DEMCR with VC_CORERESET to catch core on reset exit
    // DEMCR = 0xE000EDFC, VC_CORERESET = bit 0, TRCENA = bit 24
    #define DEMCR_ADDR       0xE000EDFC
    #define DEMCR_TRCENA     (1U << 24)
    #define DEMCR_VC_HARDERR (1U << 10)
    #define DEMCR_VC_CORERESET (1U << 0)
    uint32_t demcr = DEMCR_TRCENA | DEMCR_VC_HARDERR | DEMCR_VC_CORERESET;
    mem_write32(DEMCR_ADDR, demcr);

    // Step 6: Release reset — core will halt at reset vector due to VC_CORERESET
    swd_nrst_release();
    sleep_ms(10);

    // Step 7: Wait for halt (S_HALT and S_RESET_ST to clear)
    uint32_t dhcsr = 0;
    uint32_t start = to_ms_since_boot(get_absolute_time());
    while (to_ms_since_boot(get_absolute_time()) - start < 500) {
        if (!mem_read32(DHCSR, &dhcsr))
            continue;

        // Filter invalid reads
        if (dhcsr == 0xFFFFFFFF || (dhcsr & 0xF000FFF0) != 0)
            continue;

        // Wait for S_RESET_ST to clear (core has exited reset)
        if (dhcsr & (1U << 25))
            continue;

        // Check S_HALT and C_DEBUGEN
        if ((dhcsr & ((1U << 17) | (1U << 0))) == ((1U << 17) | (1U << 0))) {
            printf("[SWD] CUR: Halted at reset vector, DHCSR=0x%08X\r\n", (unsigned)dhcsr);

            // Freeze IWDG and WWDG in debug mode via DBGMCU_CR.
            // STM32-only register (0xE0042000 range, same block as
            // DBG_IDCODE) — not mapped on other Cortex-M families
            // (e.g. BAT32G135), so only touch it when the selected
            // target is actually an STM32.
            extern target_type_t target_get_type(void);
            if (target_is_stm32(target_get_type())) {
                #define DBGMCU_CR 0xE0042004
                uint32_t dbg_cr;
                mem_read32(DBGMCU_CR, &dbg_cr);
                dbg_cr |= (1U << 8) | (1U << 9);  // DBG_IWDG_STOP | DBG_WWDG_STOP
                mem_write32(DBGMCU_CR, dbg_cr);
            }

            return true;
        }
    }

    printf("[SWD] CUR: Halt timeout, DHCSR=0x%08X\r\n", (unsigned)dhcsr);
    return false;
}

void swd_nrst_assert(void) {
    gpio_init(SWD_NRST_PIN);
    gpio_set_dir(SWD_NRST_PIN, GPIO_OUT);
    gpio_put(SWD_NRST_PIN, 0);
}

void swd_nrst_release(void) {
    gpio_init(SWD_NRST_PIN);
    gpio_set_dir(SWD_NRST_PIN, GPIO_IN);  // High-Z, target pull-up
    gpio_disable_pulls(SWD_NRST_PIN);
}

void swd_nrst_pulse(uint32_t ms) {
    swd_nrst_assert();
    sleep_ms(ms);
    swd_nrst_release();
}

bool swd_read_dp(uint8_t addr, uint32_t *value) {
    if (!initialized)
        return false;

    uint8_t request = make_request(false, true, addr);

    for (int retry = 0; retry < 100; retry++) {
        swd_seq_out(request, 8);
        last_ack = swd_seq_in(3);

        if (last_ack == SWD_ACK_WAIT) {
            swd_turnaround(SWDIO_DRIVE);
            swd_seq_out(0, 8);
            continue;
        }
        if (last_ack != SWD_ACK_OK) {
            swd_turnaround(SWDIO_DRIVE);
            return false;
        }

        bool parity_ok = swd_seq_in_parity(value);
        swd_seq_out(0, 8);
        return parity_ok;
    }
    // WAIT timeout
    swd_write_dp(DP_ABORT, 0x1E);
    return false;
}

bool swd_write_dp(uint8_t addr, uint32_t value) {
    if (!initialized)
        return false;

    uint8_t request = make_request(false, false, addr);

    for (int retry = 0; retry < 100; retry++) {
        swd_seq_out(request, 8);
        last_ack = swd_seq_in(3);

        if (last_ack == SWD_ACK_WAIT) {
            swd_turnaround(SWDIO_DRIVE);
            swd_seq_out(0, 8);
            continue;
        }
        if (last_ack != SWD_ACK_OK) {
            swd_turnaround(SWDIO_DRIVE);
            return false;
        }

        swd_seq_out_parity(value);
        swd_seq_out(0, 8);
        return true;
    }
    // WAIT timeout — clear sticky overrun
    uint8_t abort_req = make_request(false, false, DP_ABORT);
    swd_seq_out(abort_req, 8);
    swd_seq_in(3);
    swd_turnaround(SWDIO_DRIVE);
    swd_seq_out_parity(0x1E);
    swd_seq_out(0, 8);
    return false;
}

// Set SELECT register for AP access
static bool swd_select_ap(uint8_t ap, uint8_t addr) {
    uint32_t select = ((uint32_t)ap << 24) | (addr & 0xF0);
    if (select != current_select) {
        if (!swd_write_dp(DP_SELECT, select))
            return false;
        current_select = select;
    }
    return true;
}

bool swd_read_ap(uint8_t ap, uint8_t addr, uint32_t *value) {
    if (!initialized)
        return false;

    if (!swd_select_ap(ap, addr))
        return false;

    // First AP read is posted - do dummy read with WAIT retry
    uint8_t request = make_request(true, true, addr & 0xC);

    for (int retry = 0; retry < 100; retry++) {
        swd_seq_out(request, 8);
        last_ack = swd_seq_in(3);

        if (last_ack == SWD_ACK_WAIT) {
            swd_turnaround(SWDIO_DRIVE);
            swd_seq_out(0, 8);
            continue;
        }
        if (last_ack != SWD_ACK_OK) {
            swd_turnaround(SWDIO_DRIVE);
            return false;
        }

        uint32_t dummy;
        swd_seq_in_parity(&dummy);
        swd_seq_out(0, 8);

        // Read RDBUFF to get actual value
        return swd_read_dp(DP_RDBUFF, value);
    }
    swd_write_dp(DP_ABORT, 0x1E);
    return false;
}

bool swd_write_ap(uint8_t ap, uint8_t addr, uint32_t value) {
    if (!initialized)
        return false;

    if (!swd_select_ap(ap, addr))
        return false;

    uint8_t request = make_request(true, false, addr & 0xC);

    for (int retry = 0; retry < 100; retry++) {
        swd_seq_out(request, 8);
        last_ack = swd_seq_in(3);

        if (last_ack == SWD_ACK_WAIT) {
            swd_turnaround(SWDIO_DRIVE);
            swd_seq_out(0, 8);
            continue;
        }
        if (last_ack != SWD_ACK_OK) {
            swd_turnaround(SWDIO_DRIVE);
            return false;
        }

        swd_seq_out_parity(value);
        swd_seq_out(0, 8);
        return true;
    }
    swd_write_dp(DP_ABORT, 0x1E);
    return false;
}

// Base CSW value — read from AP defaults during init, like BMP's adiv5_new_ap.
// Only size/addrinc are modified per-operation; all other bits preserved from AP default.
static uint32_t ap_csw_base = 0;

// CSW bit masks (from BMP adiv5.h)
#define CSW_SIZE_MASK     (7U << 0)
#define CSW_ADDRINC_MASK  (3U << 4)
#define CSW_SIZE_HALFWORD (1U << 0)
#define CSW_SIZE_WORD     (2U << 0)
#define CSW_ADDRINC_SINGLE (1U << 4)
#define CSW_DBGSWENABLE   (1U << 31)
#define CSW_MTE           (1U << 15)
#define CSW_HNOSEC        (1U << 30)

// Initialize AHB-AP for memory access.
static bool swd_init_ahb_ap_ex(bool fast) {
    uint32_t stat;

    if (fast) {
        // Race path: skip the abort-clear (swd_connect_ex(true) already
        // did one right after reading DPIDR) and the power-down request +
        // ack-wait -- the debug domain is guaranteed powered down right
        // after the hardware reset swd_race_once() just performed a
        // moment ago. Every transaction cut here is time still spent
        // inside the race window instead of proving what the reset
        // already guarantees (observed on real BAT32G135 hardware:
        // 13/13 dp_only hits across a 2550-shot sweep never got past this
        // function -- the window was closing before it returned).
    } else {
        // Clear sticky errors first (BMP: adiv5_dp_abort before dp_init)
        swd_write_dp(DP_ABORT, 0x1E);

        // Step 1: Power DOWN debug domain (BMP: adiv5_dp_write(dp, CTRLSTAT, 0))
        if (!swd_write_dp(DP_CTRL_STAT, 0))
            return false;

        // Wait for power-down acknowledge (ACK bits clear). Sleep is at the
        // TAIL of this loop (after the check), so on the common case —
        // domain already down after a reset — it costs nothing.
        for (int i = 0; i < 250; i++) {
            if (!swd_read_dp(DP_CTRL_STAT, &stat))
                return false;
            if (!(stat & 0xA0000000))
                break;
            sleep_ms(1);
        }
    }

    // Step 2: Power UP (BMP: CSYSPWRUPREQ | CDBGPWRUPREQ)
    if (!swd_write_dp(DP_CTRL_STAT, 0x50000000))
        return false;

    // Wait for power-up acknowledge.
    // fast (race path): the unconditional sleep_ms(1) BEFORE the first check
    // (paid even when the domain has already acked) is the single largest
    // fixed cost on the reset-release -> first-memory-read critical path.
    // Check immediately, then fall back to short bounded polling instead.
    if (fast) {
        if (!swd_read_dp(DP_CTRL_STAT, &stat))
            return false;
        for (int i = 0; i < 400 && (stat & 0xA0000000) != 0xA0000000; i++) {
            busy_wait_us_32(5);
            if (!swd_read_dp(DP_CTRL_STAT, &stat))
                return false;
        }
    } else {
        // Wait for power-up acknowledge (BMP: polls with 10ms delay)
        for (int i = 0; i < 200; i++) {
            sleep_ms(1);
            if (!swd_read_dp(DP_CTRL_STAT, &stat))
                return false;
            if ((stat & 0xA0000000) == 0xA0000000)
                break;
        }
    }

    if ((stat & 0xA0000000) != 0xA0000000)
        return false;

    // Clear sticky errors after power cycle
    swd_write_dp(DP_ABORT, 0x1E);

    // BMP: adiv5_new_ap reads IDR, BASE, CSW from AP defaults.
    // The IDR value is discarded -- nothing in this file ever reads it --
    // so on the race path it is a whole AP read (posted read + RDBUFF DP
    // read, plus a DP_SELECT write since IDR is in APBANKSEL 0xF while CSW
    // is in bank 0) bought for nothing. Kept off the fast path only; the
    // fast=false path is unchanged so the STM32/LPC workflows keep the
    // exact bring-up they were validated with.
    if (!fast) {
        uint32_t ap_idr;
        swd_read_ap(0, AP_IDR, &ap_idr);
    }

    uint32_t csw_default;
    swd_read_ap(0, AP_CSW, &csw_default);

    // BMP: csw &= ~(SIZE_MASK | ADDRINC_MASK | MTE | HNOSEC); csw |= DBGSWENABLE
    ap_csw_base = csw_default;
    ap_csw_base &= ~(CSW_SIZE_MASK | CSW_ADDRINC_MASK | CSW_MTE | CSW_HNOSEC);
    ap_csw_base |= CSW_DBGSWENABLE;

    return true;
}

static bool swd_init_ahb_ap(void) {
    return swd_init_ahb_ap_ex(false);
}

// BMP: ap_mem_access_setup — writes CSW + TAR before every memory operation
static bool swd_mem_access_setup(uint32_t addr) {
    uint32_t csw = ap_csw_base | CSW_SIZE_WORD | CSW_ADDRINC_SINGLE;

    // Write CSW (BMP: adiv5_ap_write(ap, CSW, csw))
    if (!swd_write_ap(0, AP_CSW, csw))
        return false;

    // Write TAR (BMP: adiv5_dp_write(dp, TAR, addr) — uses AP write since TAR is in same bank)
    if (!swd_write_ap(0, AP_TAR, addr))
        return false;

    return true;
}

// ADIv5 only guarantees TAR auto-increment inside a 1 KB window: past the
// boundary the AP wraps back to the start of that window and silently
// re-serves data already read, with no error anywhere. Any transfer that
// crosses a 1 KB boundary must therefore re-arm TAR. Measured on the
// BAT32G135 bench: a 4 KB read from 0x20000020 was correct up to 0x3E0
// (= 0x20000400) and repeated itself from there on.
#define AP_TAR_WINDOW 0x400u

static uint32_t swd_window_words(uint32_t addr, uint32_t remaining) {
    uint32_t left = (AP_TAR_WINDOW - (addr & (AP_TAR_WINDOW - 1u))) / 4u;
    return remaining < left ? remaining : left;
}

uint32_t swd_read_mem(uint32_t addr, uint32_t *data, uint32_t count) {
    if (!initialized || count == 0)
        return 0;

    if (!ahb_initialized) {
        if (!swd_init_ahb_ap())
            return 0;
        ahb_initialized = true;
    }

    uint32_t read = 0;
    while (read < count) {
        uint32_t cur = addr + read * 4;
        uint32_t chunk = swd_window_words(cur, count - read);

        // BMP: setup CSW + TAR before access — and again at every window
        if (!swd_mem_access_setup(cur))
            break;

        uint32_t got = 0;
        while (got < chunk && swd_read_ap(0, AP_DRW, &data[read + got]))
            got++;
        read += got;
        if (got < chunk)
            break;
    }

    return read;
}

uint32_t swd_write_mem(uint32_t addr, const uint32_t *data, uint32_t count) {
    if (!initialized || count == 0)
        return 0;

    if (!ahb_initialized) {
        if (!swd_init_ahb_ap())
            return 0;
        ahb_initialized = true;
    }

    uint32_t written = 0;
    while (written < count) {
        uint32_t cur = addr + written * 4;
        uint32_t chunk = swd_window_words(cur, count - written);

        // Same 1 KB TAR window as in swd_read_mem() — a write that crosses
        // it would land back at the start of the window, corrupting data
        // already written instead of continuing.
        if (!swd_mem_access_setup(cur))
            break;

        uint32_t done = 0;
        while (done < chunk && swd_write_ap(0, AP_DRW, data[written + done]))
            done++;

        // BMP: flush write buffer by reading RDBUFF
        uint32_t dummy;
        swd_read_dp(DP_RDBUFF, &dummy);

        written += done;
        if (done < chunk)
            break;
    }

    return written;
}

// --- Reset-release race (RESETB vs SWD) ---
//
// swd_connect_under_reset() (above) connects to the target WHILE nRST is
// held low, then releases — that only works if the target's SW-DP still
// answers with the reset asserted. Some parts (e.g. BAT32G135 per its
// user manual — SWDIO/SWCLK are high-impedance during an external reset
// or POR) do not. For those, the only way in is the inverse: release
// nRST, then race the target's own firmware to connect over SWD before
// it can lock the interface (e.g. writing a SWD-disable bit at runtime).
// This is a pure timing race, not a fault injection — see
// 07_BAT32G135_FAULTYCAT.md §9/§9bis/§9ter for the full rationale.

bool swd_race_persistent(uint32_t delay_us, uint32_t addr,
                          uint32_t *value, uint8_t *ack) {
    if (value) *value = 0;
    if (ack) *ack = 0;

    if (!connected || !ahb_initialized) {
        printf("[RACE-P] needs an established connection + AHB-AP first\r\n");
        return false;
    }

    // A partir d'ici tout est chronometre : cette variante existe pour que
    // le premier acces memoire tombe quelques microsecondes apres le front
    // de reset, au lieu de 250. Une interruption Wi-Fi y serait du meme
    // ordre de grandeur que la fenetre mesuree. Sortie garantie par
    // l'attribut cleanup malgre les `return` qui suivent.
    NET_QUIET_SECTION;

    // Pre-arm the AP so that after the reset edge only the DRW read
    // remains. CSW/TAR are written here, BEFORE nRST — that is the whole
    // point of this variant.
    uint32_t csw = ap_csw_base | CSW_SIZE_WORD | CSW_ADDRINC_SINGLE;
    if (!swd_write_ap(0, AP_CSW, csw) || !swd_write_ap(0, AP_TAR, addr)) {
        printf("[RACE-P] pre-arm of CSW/TAR failed\r\n");
        return false;
    }

    if (phy_mode == SWD_PHY_PIO) {
        swd_phy_nrst_race(SWD_NRST_PIN, delay_us);
    } else {
        swd_nrst_assert();
        busy_wait_us_32(20);
        swd_nrst_release();
        if (delay_us)
            busy_wait_us_32(delay_us);
    }

    // Single posted AP read + RDBUFF. No line reset, no DPIDR, no AHB-AP
    // bring-up: if the DP survived nRST this is the earliest possible
    // flash access.
    uint32_t dummy = 0;
    uint8_t req = make_request(true, true, AP_DRW & 0xC);
    swd_seq_out(req, 8);
    last_ack = swd_seq_in(3);
    if (ack) *ack = last_ack;
    if (last_ack != SWD_ACK_OK) {
        swd_turnaround(SWDIO_DRIVE);
        return false;
    }
    swd_seq_in_parity(&dummy);
    swd_seq_out(0, 8);

    uint32_t v = 0;
    bool ok = swd_read_dp(DP_RDBUFF, &v);
    if (ack) *ack = last_ack;
    if (value) *value = v;
    return ok;
}

bool swd_race_once(uint32_t delay_us,
                    uint32_t sram_base, uint32_t sram_size,
                    uint32_t flash_base, uint32_t flash_size,
                    swd_race_report_t *report) {
    swd_race_report_t rep = { SWD_RACE_NO_DP, 0, 0 };

    // THIS is the parameter under test: time from reset release to the
    // start of the SWD connect sequence. Passed through exactly as
    // given — no clamping, no compensation subtracted. A sweep whose
    // low end silently gets rewritten produces a heat map that lies
    // about what was actually measured (cf. the glitch engine's PAUSE
    // 0-18 dead zone and WIDTH/GAP non-monotonicity in glitch.c — the
    // failure mode this deliberately avoids repeating).
    //
    // In SWD_PHY_PIO, the whole assert/hold-tRSL/release/wait-delay_us
    // sequence runs on PIO2 SM1 (swd_phy_nrst_race()), so the C jitter
    // between "reset released" and "start counting delay_us" — and
    // between "delay_us elapsed" and "first SWCLK edge" (SM0, same PIO2
    // instance) — is gone; the CLI layer requires SWD PHY PIO before
    // SWD RACE runs for exactly this reason. The bit-bang branch below
    // stays correct (and is what a direct, non-CLI caller would still
    // get) but is no longer reachable through the shipped CLI.
    if (phy_mode == SWD_PHY_PIO) {
        // A false return means the PIO sequencer itself never signaled
        // completion (no pull-up, pin disconnected, ...) -- proceeding
        // anyway would otherwise report a bare no_dp indistinguishable
        // from "the target locked SWD before we got there", which is
        // exactly the misdiagnosis this whole investigation started from
        // (see §0bis of 07_BAT32G135_FAULTYCAT.md). Flag it loudly instead
        // of silently folding it into the same category.
        if (!swd_phy_nrst_race(SWD_NRST_PIN, delay_us)) {
            printf("[SWD RACE] WARNING: nRST sequencer timed out (delay_us=%lu) -- "
                   "check nRST wiring/pull-up before trusting this attempt's "
                   "classification\r\n", (unsigned long)delay_us);
        }
    } else {
        // Hold reset for a fixed, short dwell — this is NOT the
        // parameter under test, just enough to guarantee a clean assert
        // (BAT32G135 tRSL minimum is 10us, [DS] §6.6; comfortably
        // covered here).
        swd_nrst_assert();
        busy_wait_us_32(20);
        swd_nrst_release();
        if (delay_us)
            busy_wait_us_32(delay_us);
    }

    // Section silencieuse a partir d'ici seulement.
    //
    // Elle ne remonte deliberement PAS jusqu'a la sequence de reset : en mode
    // PIO celle-ci est executee par PIO2 SM1 et se moque des interruptions du
    // coeur (voir le commentaire d'en-tete de cette fonction), et surtout le
    // WARNING de cablage nRST ci-dessus doit pouvoir sortir. C'est lui qui
    // distingue « nRST mal cable » de « la cible a verrouille SWD » -- la
    // confusion meme dont est partie toute cette campagne. Dans une section
    // silencieuse et le tampon plein, il serait jete et compte, donc perdu
    // pour un client TCP.
    //
    // Ce qui suit, en revanche, est du code coeur chronometre : le connect
    // doit tomber le plus tot possible apres le front de reset.
    NET_QUIET_SECTION;

    // Fast path throughout: uncompensated connect + AHB-AP bring-up.
    // In SWD_PHY_BITBANG this does NOT use clk_delay_us beyond what
    // swd_connect_ex()/swd_init_ahb_ap_ex() already force — the CLI layer
    // requires SWD PHY PIO before SWD RACE runs, since any per-bit
    // bit-bang delay (or SPEED 0's mis-sampling, see §0bis of
    // 07_BAT32G135_FAULTYCAT.md) dwarfs or corrupts the window measured.
    if (!swd_connect_ex(true)) {
        if (report) *report = rep;
        return false;
    }
    rep.result = SWD_RACE_DP_ONLY;

    ahb_initialized = false;
    if (!swd_init_ahb_ap_ex(true)) {
        if (report) *report = rep;
        return false;
    }
    ahb_initialized = true;

    uint32_t words[2] = {0, 0};
    if (swd_read_mem(0x00000000, words, 2) != 2) {
        if (report) *report = rep;
        return false;
    }
    rep.sp = words[0];
    rep.pc = words[1];

    if (rep.sp == 0x00000000 || rep.sp == 0xFFFFFFFF) {
        rep.result = SWD_RACE_MEM_BLOCKED;
        if (report) *report = rep;
        return false;
    }

    // Cortex-M reset-vector plausibility check (doc §8.2): SP inside the
    // target's SRAM, PC inside its code flash with the Thumb bit set.
    // Only applied when the caller supplied real bounds (current TARGET
    // has a descriptor) — otherwise a non-blocked read is reported as
    // "perturbed" rather than risking a false SUCCESS.
    bool have_bounds = (sram_size != 0 && flash_size != 0);
    bool plausible = have_bounds &&
        rep.sp >= sram_base && rep.sp < sram_base + sram_size &&
        rep.pc >= flash_base && rep.pc < flash_base + flash_size &&
        (rep.pc & 1);

    rep.result = plausible ? SWD_RACE_SUCCESS : SWD_RACE_PERTURBED;
    if (report) *report = rep;
    return plausible;
}

// Times the same fast connect + AHB-AP bring-up + 2-word read that
// swd_race_once() runs, without touching nRST — the "SWD BENCH" CLI
// command's before/after measurement of what a physical-layer change
// actually bought, at whatever phy mode / speed / PIO frequency is
// currently active.
bool swd_bench(uint32_t *us, swd_bench_split_t *split) {
    swd_bench_split_t sp = {0, 0, 0};
    absolute_time_t t0 = get_absolute_time();

    bool ok = swd_connect_ex(true);
    absolute_time_t t1 = get_absolute_time();
    sp.connect_us = (uint32_t)absolute_time_diff_us(t0, t1);

    if (ok) {
        ahb_initialized = false;
        ok = swd_init_ahb_ap_ex(true);
        // Must mirror swd_race_once(): swd_init_ahb_ap_ex() does NOT set
        // this itself. Forgetting it made swd_read_mem() below take the
        // !ahb_initialized branch and re-run the whole bring-up through
        // swd_init_ahb_ap() -- the fast=false variant, with its
        // unconditional sleep_ms(1) -- inside the read phase. That single
        // missing line was ~1.2ms of the "frequency-independent floor"
        // this benchmark was built to explain, and it was measuring the
        // benchmark, not the race path (which sets this correctly).
        if (ok)
            ahb_initialized = true;
    }
    absolute_time_t t2 = get_absolute_time();
    sp.ahb_us = (uint32_t)absolute_time_diff_us(t1, t2);

    if (ok) {
        uint32_t words[2] = {0, 0};
        ok = (swd_read_mem(0x00000000, words, 2) == 2);
    }
    absolute_time_t t3 = get_absolute_time();
    sp.read_us = (uint32_t)absolute_time_diff_us(t2, t3);

    if (us)
        *us = (uint32_t)absolute_time_diff_us(t0, t3);
    if (split)
        *split = sp;
    return ok;
}

bool swd_race_sweep(uint32_t start_us, uint32_t end_us, uint32_t step_us,
                     uint32_t shots,
                     uint32_t sram_base, uint32_t sram_size,
                     uint32_t flash_base, uint32_t flash_size,
                     swd_race_report_t *success_out) {
    if (step_us == 0 || end_us < start_us || shots == 0 ||
        shots > SWD_RACE_SWEEP_MAX_SHOTS)
        return false;

    uint32_t n_points = (end_us - start_us) / step_us + 1;
    if (n_points > SWD_RACE_SWEEP_MAX_POINTS)
        return false;
    uint32_t total = n_points * shots;  // bounded by the two caps above

    uint32_t no_dp = 0, dp_only = 0, mem_blocked = 0, perturbed = 0;
    uint32_t done = 0;
    uint32_t last_print_ms = to_ms_since_boot(get_absolute_time());

    for (uint32_t i = 0; i < n_points; i++) {
        uint32_t delay_us = start_us + i * step_us;

        for (uint32_t shot = 0; shot < shots; shot++) {
            // Rendez-vous reseau entre deux tirs : un sweep dure plusieurs
            // minutes, pendant lesquelles chaque tir ferme une section
            // silencieuse. Sans cette reprise, le lien TCP mourrait avant la
            // fin. Placee ici, elle est hors de toute fenetre chronometree.
            net_cli_pump();

            // Any pending input aborts the sweep — this can run for a
            // long time and there is no other way to interrupt it mid-run.
            if (getchar_timeout_us(0) != PICO_ERROR_TIMEOUT) {
                printf("\r\n[SWD RACE] aborted by user at delay=%luus (%lu/%lu)\r\n",
                       (unsigned long)delay_us, (unsigned long)done, (unsigned long)total);
                return false;
            }

            swd_race_report_t rep;
            bool success = swd_race_once(delay_us, sram_base, sram_size,
                                          flash_base, flash_size, &rep);
            done++;

            if (success) {
                printf("\r\n*** SWD RACE SUCCESS at delay=%luus (shot %lu/%lu) "
                       "SP=0x%08lX PC=0x%08lX ***\r\n",
                       (unsigned long)delay_us, (unsigned long)(shot + 1),
                       (unsigned long)shots, (unsigned long)rep.sp, (unsigned long)rep.pc);
                printf("*** TARGET LEFT POWERED + CONNECTED "
                       "-- dump it now, do not reset or power-cycle ***\r\n");
                if (success_out) *success_out = rep;
                return true;
            }

            switch (rep.result) {
                case SWD_RACE_DP_ONLY:     dp_only++;     break;
                case SWD_RACE_MEM_BLOCKED: mem_blocked++; break;
                case SWD_RACE_PERTURBED:   perturbed++;   break;
                case SWD_RACE_NO_DP:
                default:                   no_dp++;       break;
            }
        }

        uint32_t now_ms = to_ms_since_boot(get_absolute_time());
        if (now_ms - last_print_ms >= 500 || i == n_points - 1) {
            printf("\r[SWD RACE] delay=%luus (%lu/%lu) no_dp=%lu dp_only=%lu "
                   "blocked=%lu perturbed=%lu",
                   (unsigned long)delay_us, (unsigned long)done, (unsigned long)total,
                   (unsigned long)no_dp, (unsigned long)dp_only,
                   (unsigned long)mem_blocked, (unsigned long)perturbed);
            last_print_ms = now_ms;
        }
    }

    printf("\r\n[SWD RACE] sweep complete, no success. no_dp=%lu dp_only=%lu "
           "blocked=%lu perturbed=%lu\r\n",
           (unsigned long)no_dp, (unsigned long)dp_only,
           (unsigned long)mem_blocked, (unsigned long)perturbed);
    return false;
}

uint8_t swd_get_last_ack(void) {
    return last_ack;
}

bool swd_clear_errors(void) {
    // Write ABORT: clear all error flags
    return swd_write_dp(DP_ABORT, 0x1E);
}

uint32_t swd_identify(void) {
    if (!swd_connect())
        return 0;

    uint32_t dpidr;
    if (!swd_read_dp(DP_DPIDR, &dpidr))
        return 0;

    return dpidr;
}

// --- Cortex-M debug operations ---

// Helper: read a single 32-bit word from target memory
static bool mem_read32(uint32_t addr, uint32_t *val) {
    return swd_read_mem(addr, val, 1) == 1;
}

// Helper: write a single 32-bit word to target memory
static bool mem_write32(uint32_t addr, uint32_t val) {
    return swd_write_mem(addr, &val, 1) == 1;
}

// Helper: write a 16-bit halfword to target memory (needed for F1 option bytes)
static bool mem_write16(uint32_t addr, uint16_t val) {
    if (!ahb_initialized) {
        if (!swd_init_ahb_ap())
            return false;
        ahb_initialized = true;
    }

    // Set CSW for halfword access
    uint32_t csw = ap_csw_base | CSW_SIZE_HALFWORD | CSW_ADDRINC_SINGLE;
    if (!swd_write_ap(0, AP_CSW, csw))
        return false;

    // Write TAR
    if (!swd_write_ap(0, AP_TAR, addr))
        return false;

    // Write DRW — data must be lane-aligned for halfword
    // For halfword at addr & 2 == 0: data in bits [15:0]
    // For halfword at addr & 2 == 2: data in bits [31:16]
    uint32_t drw = (addr & 2) ? ((uint32_t)val << 16) : (uint32_t)val;
    if (!swd_write_ap(0, AP_DRW, drw))
        return false;

    // Flush
    uint32_t dummy;
    swd_read_dp(DP_RDBUFF, &dummy);

    // Restore word-size CSW
    csw = ap_csw_base | CSW_SIZE_WORD | CSW_ADDRINC_SINGLE;
    swd_write_ap(0, AP_CSW, csw);

    return true;
}

bool swd_halt(void) {
    // BMP-style robust halt: loop until S_HALT is confirmed.
    // Step 1: Enable debug first (C_DEBUGEN without C_HALT)
    if (!mem_write32(DHCSR, DBGKEY | 0x1))
        return false;

    // Step 2: Read back to flush
    uint32_t dhcsr;
    mem_read32(DHCSR, &dhcsr);

    // Step 3: Loop - write C_DEBUGEN|C_HALT, read back, check S_HALT
    bool reset_seen = false;
    uint32_t start = to_ms_since_boot(get_absolute_time());
    while (to_ms_since_boot(get_absolute_time()) - start < 500) {
        // Write halt request
        if (!mem_write32(DHCSR, DBGKEY | 0x3))
            return false;

        // Read back DHCSR
        if (!mem_read32(DHCSR, &dhcsr))
            return false;

        // Filter invalid reads (errata on some STM32)
        if (dhcsr == 0xFFFFFFFF || (dhcsr & 0xF000FFF0) != 0)
            continue;

        // Check for reset - need to see it clear before accepting halt
        if ((dhcsr & (1 << 25)) && !reset_seen) {  // S_RESET_ST
            reset_seen = true;
            continue;
        }

        // Check if halt succeeded
        if ((dhcsr & ((1 << 17) | (1 << 0))) == ((1 << 17) | (1 << 0)))  // S_HALT | C_DEBUGEN
            return true;
    }

    printf("[SWD] Halt timeout, DHCSR=0x%08X\r\n", (unsigned)dhcsr);
    return false;
}

bool swd_resume(void) {
    // C_DEBUGEN only (clear C_HALT)
    return mem_write32(DHCSR, DBGKEY | 0x1);
}

bool swd_read_core_reg(uint8_t reg, uint32_t *value) {
    // Write register index to DCRSR (REGWnR=0 for read)
    if (!mem_write32(DCRSR, (uint32_t)reg))
        return false;

    // Wait for S_REGRDY
    for (int i = 0; i < 100; i++) {
        uint32_t dhcsr;
        if (!mem_read32(DHCSR, &dhcsr))
            return false;
        if (dhcsr & (1 << 16))  // S_REGRDY
            return mem_read32(DCRDR, value);
        sleep_us(10);
    }
    return false;
}

bool swd_write_core_reg(uint8_t reg, uint32_t value) {
    // Write value to DCRDR
    if (!mem_write32(DCRDR, value))
        return false;

    // Write register index to DCRSR with REGWnR=1 (bit 16) for write
    if (!mem_write32(DCRSR, (uint32_t)reg | (1 << 16)))
        return false;

    // Wait for S_REGRDY
    for (int i = 0; i < 100; i++) {
        uint32_t dhcsr;
        if (!mem_read32(DHCSR, &dhcsr))
            return false;
        if (dhcsr & (1 << 16))  // S_REGRDY
            return true;
        sleep_us(10);
    }
    return false;
}

bool swd_detect(uint32_t *cpuid_out, uint32_t *dbg_idcode_out) {
    if (!ahb_initialized) {
        if (!swd_init_ahb_ap())
            return false;
        ahb_initialized = true;
    }

    if (cpuid_out)
        mem_read32(CPUID, cpuid_out);

    // DBG_IDCODE (0xE0042000) is an STM32-specific debug ID register, not
    // mapped on other Cortex-M families. Read it when the target is still
    // unknown (STM32 auto-detection in progress, command_parser.c's
    // "SWD IDCODE" handler) or already known to be an STM32; for a target
    // explicitly set to something else (e.g. BAT32G135), report "unknown"
    // instead of reading an address that has no defined meaning there.
    if (dbg_idcode_out) {
        extern target_type_t target_get_type(void);
        target_type_t tt = target_get_type();
        if (tt == TARGET_NONE || target_is_stm32(tt))
            mem_read32(DBG_IDCODE, dbg_idcode_out);
        else
            *dbg_idcode_out = 0;
    }

    return true;
}

// --- BAT32G135 flash controller (FMC) ---
//
// Transcribed from Cmsemicon's OWN driver, Driver/src/flash.c of the
// official CMSIS pack (Cmsemicon.BAT32G135.1.0.4.pack, SVD confirms
// FMC base 0x40020000). That source is authoritative and corrects
// 07_BAT32G135_FAULTYCAT.md §7.4 on several points:
//
//   * ★ Programming granularity is a BYTE, not a 32-bit word. The vendor's
//     ProgramPage() walks a uint8_t* and re-arms FLOPMD1/FLOPMD2 before
//     EVERY byte. The doc's "Program (mot 32 bits)" is wrong. This is what
//     lets us set OCDEN alone without disturbing the WDT/LVD/HOCO bytes
//     sharing its word.
//   * FLERMD is 0x08 for chip erase (doc right) but ALSO 0x10 for sector
//     erase (doc silent) — and must be restored to 0x00 afterwards.
//   * FLPROT is 0xF1 to unlock and 0xF0 to re-lock (doc gave only 0xF1).
//   * The six timing registers (FLCERCNT/FLSERCNT/FLNVSCNT/FLPROCNT/
//     FLPRVCNT/FLERVCNT) are never touched by the vendor driver — their
//     resets are usable. Do not program them.
//
// ⚠ Sector erase is deliberately NOT implemented. OCDEN lives at
// 0x000000C3, inside sector 0, which also holds the reset vector table.
#define FMC_FLSTS       0x40020000u
#define FMC_FLOPMD1     0x40020004u
#define FMC_FLOPMD2     0x40020008u
#define FMC_FLERMD      0x4002000Cu
#define FMC_FLPROT      0x40020020u

#define FMC_FLSTS_OVF   (1u << 0)
#define FMC_FLSTS_EVF   (1u << 2)

#define FMC_FLPROT_UNLOCK  0xF1u
#define FMC_FLPROT_LOCK    0xF0u
#define FMC_FLERMD_CHIP    0x08u
#define FMC_FLERMD_SECTOR  0x10u   /* vendor Driver/src/flash.c; absent from doc §7.4 */
#define BAT32_DATA_FLASH_BASE 0x00500000u
#define FMC_FLERMD_NONE    0x00u

// Byte-width AHB-AP write. Models mem_write16() above: CSW SIZE=BYTE (0)
// and the data byte placed in the lane selected by addr[1:0].
static bool mem_write8(uint32_t addr, uint8_t val) {
    if (!ahb_initialized) {
        if (!swd_init_ahb_ap())
            return false;
        ahb_initialized = true;
    }

    uint32_t csw = ap_csw_base | CSW_ADDRINC_SINGLE;  // SIZE field 0 = byte
    if (!swd_write_ap(0, AP_CSW, csw))
        return false;
    if (!swd_write_ap(0, AP_TAR, addr))
        return false;

    uint32_t drw = (uint32_t)val << ((addr & 3u) * 8u);
    if (!swd_write_ap(0, AP_DRW, drw))
        return false;

    uint32_t dummy;
    swd_read_dp(DP_RDBUFF, &dummy);

    csw = ap_csw_base | CSW_SIZE_WORD | CSW_ADDRINC_SINGLE;
    swd_write_ap(0, AP_CSW, csw);
    return true;
}

// Poll FLSTS.OVF, then clear it. Bounded: a flash op that never reports
// completion must not wedge the CLI. Datasheet timings ([DS] §6.9.1):
// byte/word program 24-30us, chip erase 20-40ms.
static bool fmc_wait_ovf(uint32_t timeout_ms) {
    uint32_t deadline = to_ms_since_boot(get_absolute_time()) + timeout_ms;
    for (;;) {
        uint32_t sts = 0;
        if (!mem_read32(FMC_FLSTS, &sts))
            return false;
        if (sts & FMC_FLSTS_OVF) {
            mem_write32(FMC_FLSTS, FMC_FLSTS_OVF);  // W1C
            return true;
        }
        if (to_ms_since_boot(get_absolute_time()) > deadline)
            return false;
        sleep_us(50);
    }
}

bool swd_bat32_flash_program(uint32_t addr, const uint8_t *data, uint32_t len) {
    if (len == 0)
        return false;

    // Clear any latched sticky error before anything else. At protection
    // Level 1 a flash read comes back FAULT (ACK=0x4), which latches
    // STICKYERR in CTRL/STAT and makes EVERY subsequent AP transaction
    // fail until DP_ABORT clears it. Without this, a caller that probed
    // the target first (the CLI reads the byte back to show before/after)
    // poisons the link and the halt below fails for a reason that has
    // nothing to do with the flash controller — which is exactly how the
    // first Level 1 recovery attempt was misdiagnosed.
    swd_clear_errors();

    // Halt the core first. The vendor driver runs from RAM with interrupts
    // off precisely because the CPU must not fetch from flash while flash
    // is being programmed; over SWD the equivalent guarantee is a halted
    // core. Without this the sensor's own firmware keeps executing out of
    // the array we are writing.
    if (!swd_halt()) {
        printf("[BAT32] program: core halt failed — refusing to write\r\n");
        return false;
    }

    if (!mem_write32(FMC_FLPROT, FMC_FLPROT_UNLOCK))
        return false;

    bool ok = true;
    for (uint32_t i = 0; i < len && ok; i++) {
        // FLOPMD1/FLOPMD2 must be re-armed before EACH byte (vendor
        // ProgramPage does exactly this inside its loop).
        ok = mem_write32(FMC_FLOPMD1, 0xAAu) &&
             mem_write32(FMC_FLOPMD2, 0x55u) &&
             mem_write8(addr + i, data[i]) &&
             fmc_wait_ovf(50);
        if (!ok)
            printf("[BAT32] program: failed at offset %lu (addr 0x%08X)\r\n",
                   (unsigned long)i, (unsigned)(addr + i));
    }

    mem_write32(FMC_FLPROT, FMC_FLPROT_LOCK);  // re-lock even on failure
    return ok;
}

// One erase pass. `trigger` is the dummy-write address, and it is what
// selects WHICH array gets erased -- see swd_bat32_chip_erase() below.
static bool bat32_erase_pass(uint32_t trigger) {
    // Order per the vendor EraseChip(): FLERMD first, then FLPROT, then
    // the FLOPMD pair (0x55/0xAA — the INVERSE of the program pair), then
    // a dummy write to trigger it.
    bool ok = mem_write32(FMC_FLERMD, FMC_FLERMD_CHIP) &&
              mem_write32(FMC_FLPROT, FMC_FLPROT_UNLOCK) &&
              mem_write32(FMC_FLOPMD1, 0x55u) &&
              mem_write32(FMC_FLOPMD2, 0xAAu) &&
              mem_write32(trigger, 0xFFFFFFFFu) &&
              fmc_wait_ovf(500);   // [DS]: chip erase 20-40ms, wide margin

    mem_write32(FMC_FLERMD, FMC_FLERMD_NONE);
    mem_write32(FMC_FLPROT, FMC_FLPROT_LOCK);
    return ok;
}

// Sector erase — the vendor driver's FLERMD=0x10, which this firmware never
// wired up. It is the only way to blank the DATA flash: the chip erase
// (FLERMD=0x08) leaves that array untouched no matter which address triggers
// it (measured 2026-09-02, both 0x00000000 and 0x00500000).
//
// `addr` selects the sector: any address inside it. Erasing a sector of CODE
// flash is equally possible and equally destructive, hence CONFIRM at the CLI.
bool swd_bat32_sector_erase(uint32_t addr) {
    swd_clear_errors();

    if (!swd_halt())
        printf("[BAT32] sector erase: core halt failed, proceeding anyway\r\n");

    bool ok = mem_write32(FMC_FLERMD, FMC_FLERMD_SECTOR) &&
              mem_write32(FMC_FLPROT, FMC_FLPROT_UNLOCK) &&
              mem_write32(FMC_FLOPMD1, 0x55u) &&
              mem_write32(FMC_FLOPMD2, 0xAAu) &&
              mem_write32(addr, 0xFFFFFFFFu) &&
              fmc_wait_ovf(100);   // [DS]: sector erase 4-5 ms, wide margin

    mem_write32(FMC_FLERMD, FMC_FLERMD_NONE);
    mem_write32(FMC_FLPROT, FMC_FLPROT_LOCK);
    return ok;
}

// ★ Measured 2026-09-02: this erases the CODE flash ONLY. After a chip erase
// that reported success, 0x00500008 still held the sensor's pairing record
// (AA 55 AA 55 "device_id"), byte-identical to the pre-erase dump -- and
// re-running the same sequence with the trigger write aimed at 0x00500000
// changed nothing either. The data flash array is simply outside what
// FLERMD=0x08 covers; only FLERMD=0x10 (swd_bat32_sector_erase) reaches it.
// The firmware's own "code + data flash" message and doc §7.4 were both
// wrong on this until now.
bool swd_bat32_chip_erase(void) {
    swd_clear_errors();   // same sticky-error trap as in program(), above

    // Halting is preferred but NOT required here: chip erase is the only
    // documented way out of Level 1, and at Level 1 the core often cannot
    // be halted at all. Refusing on a failed halt would make the escape
    // hatch unusable exactly when it is needed. Erasing under a running
    // core is acceptable because the whole array is going away regardless.
    if (!swd_halt())
        printf("[BAT32] chip erase: core halt failed, proceeding anyway\r\n");

    // One pass, triggered in code flash. A second pass aimed at the data
    // flash was tried and erased nothing, so it is not kept: this verb does
    // what it does, and SECTORERASE is what blanks 0x500000.
    //
    // Deliberately NOT chaining the three SECTORERASE calls here. Escaping
    // Level 1 costs a chip erase, and the part happens to keep its data
    // flash through it — destroying that on the caller's behalf would throw
    // away the pairing the hardware was willing to preserve.
    bool ok = bat32_erase_pass(0x00000000u);
    if (!ok)
        printf("[BAT32] chip erase: code flash pass failed\r\n");
    return ok;
}

// --- BAT32G135 Level 1 bypass: read flash THROUGH the core ---
//
// Rationale, all three premises measured on hardware (see §2bis of
// TPLink_Tapo/07_BAT32G135_FAULTYCAT.md):
//   1. at Level 1 SRAM stays fully readable — the protection only ever
//      covered "données flash" (§2.3), which turns out to be literal;
//   2. the core can still be halted at Level 1;
//   3. the core itself may read flash — it executes from it. The
//      protection targets the DEBUGGER's view, not the CPU's.
// So: park a tiny copier in SRAM, point the core at it, let IT read the
// flash, and read the result back out of SRAM. No fault injection.
//
// ⚠ UNTESTED. Premise (1) and (2) are measured; what is NOT yet verified
// is that SRAM is WRITABLE at Level 1, that PC/SP writes take, and that
// this ARMv6-M will execute from SRAM. Any of those failing sinks it —
// the function reports which one rather than guessing.

#define BAT32_PAYLOAD_ADDR  0x20000000u
#define BAT32_PAYLOAD_BUF   0x20000020u
#define BAT32_PAYLOAD_SP    0x20002000u
#define BAT32_PAYLOAD_MAXW  1024u          /* 4 KB per pass */

bool swd_bat32_ram_read(uint32_t src, uint32_t words, uint32_t *out) {
    if (!out || words == 0 || words > BAT32_PAYLOAD_MAXW)
        return false;
    // `ldr r3,[r0]` faults on a misaligned address on ARMv6-M, and that
    // fault would surface below as "payload did not reach BKPT" -- a
    // message that reads as "the bypass does not work" when in fact the
    // argument is wrong.
    if (src & 3u) {
        printf("[BAT32-RAM] source 0x%08X is not word-aligned\r\n", (unsigned)src);
        return false;
    }

    swd_clear_errors();
    if (!swd_halt()) {
        printf("[BAT32-RAM] core halt failed\r\n");
        return false;
    }

    // Thumb-16 copier, 4 words. r0=src r1=dst r2=count:
    //   6803  ldr  r3,[r0]      600B  str  r3,[r1]
    //   3004  adds r0,#4        3104  adds r1,#4
    //   3A01  subs r2,#1        D1F9  bne  -14 (back to ldr)
    //   BE00  bkpt #0           BE00  bkpt #0
    static const uint32_t payload[4] = {
        0x600B6803u, 0x31043004u, 0xD1F93A01u, 0xBE00BE00u
    };
    if (swd_write_mem(BAT32_PAYLOAD_ADDR, payload, 4) != 4) {
        printf("[BAT32-RAM] SRAM not writable — payload injection refused\r\n");
        return false;
    }
    uint32_t check[4] = {0, 0, 0, 0};
    if (swd_read_mem(BAT32_PAYLOAD_ADDR, check, 4) != 4 ||
        check[0] != payload[0] || check[3] != payload[3]) {
        printf("[BAT32-RAM] payload readback mismatch (%08X %08X)\r\n",
               (unsigned)check[0], (unsigned)check[3]);
        return false;
    }

    bool ok = swd_write_core_reg(13, BAT32_PAYLOAD_SP) &&
              swd_write_core_reg(0, src) &&
              swd_write_core_reg(1, BAT32_PAYLOAD_BUF) &&
              swd_write_core_reg(2, words) &&
              swd_write_core_reg(15, BAT32_PAYLOAD_ADDR | 1u);   // Thumb bit
    if (!ok) {
        printf("[BAT32-RAM] core register write failed\r\n");
        return false;
    }
    uint32_t xpsr = 0;
    swd_read_core_reg(16, &xpsr);
    xpsr |= (1u << 24);                    // T bit — ARMv6-M has no ARM state
    swd_write_core_reg(16, xpsr);

    // TRCENA + VC_HARDERR. The vector catch is what makes a failure
    // legible: without it "this core will not execute from SRAM" and "the
    // payload hung" are the same 500 ms timeout, whereas a caught hard
    // fault halts at once and PC says where it died.
    uint32_t demcr = (1u << 24) | DEMCR_VC_HARDERR;
    swd_write_mem(DEMCR, &demcr, 1);

    if (!swd_resume()) {
        printf("[BAT32-RAM] resume failed\r\n");
        return false;
    }

    // Wait for the BKPT to halt us again. 4 KB at a few MHz core clock is
    // sub-millisecond; be generous but bounded.
    bool halted = false, lockup = false;
    uint32_t dhcsr = 0;
    uint32_t start = to_ms_since_boot(get_absolute_time());
    while (to_ms_since_boot(get_absolute_time()) - start < 500) {
        if (mem_read32(DHCSR, &dhcsr)) {
            if (dhcsr & (1u << 17)) { halted = true; break; }   // S_HALT
            if (dhcsr & (1u << 19)) { lockup = true; break; }   // S_LOCKUP
        }
        sleep_us(200);
    }
    if (!halted) {
        printf("[BAT32-RAM] payload did not reach BKPT (%s, DHCSR=0x%08X)\r\n",
               lockup ? "core LOCKED UP" : "core still running", (unsigned)dhcsr);
        swd_halt();
        return false;
    }

    // WHERE it stopped decides whether the buffer holds flash or garbage.
    // A clean finish halts ON one of the two trailing BKPTs (PC points at
    // the BKPT, not past it): payload+0x0C or +0x0E. Any other PC means the
    // copier faulted or never ran, and the buffer is stale SRAM.
    uint32_t pc = 0;
    if (!swd_read_core_reg(15, &pc)) {
        // Never fall through to the buffer here: a buffer nobody vouched
        // for is the one failure that LOOKS like success. At Level 1 the
        // SRAM it would return plausibly holds `.data` copied from flash
        // at boot, i.e. flash-derived bytes the payload never fetched.
        printf("[BAT32-RAM] PC unreadable after halt -- refusing the buffer\r\n");
        return false;
    }
    if (pc < BAT32_PAYLOAD_ADDR + 0x0Cu || pc > BAT32_PAYLOAD_ADDR + 0x0Eu) {
        uint32_t dfsr = 0;
        mem_read32(0xE000ED30u, &dfsr);   // DFSR: bit1 BKPT, bit3 VCATCH
        printf("[BAT32-RAM] halted at PC=0x%08X, not the trailing BKPT "
               "(DFSR=0x%08X) -- buffer would not be flash data\r\n",
               (unsigned)pc, (unsigned)dfsr);
        return false;
    }

    return swd_read_mem(BAT32_PAYLOAD_BUF, out, words) == words;
}

// --- BAT32G135 operations (read-only) ---

bool swd_bat32_read_options(const bat32_target_info_t *info) {
    // Cluster 0 option bytes (0x000000C0-C3): one aligned word.
    // Byte layout (little-endian, [UM] §28.2): WDT | LVD | HOCO | OCDEN.
    uint32_t word0 = 0;
    bool have0 = mem_read32(info->ocden_addr & ~0x3u, &word0);
    uint8_t ocden0 = (uint8_t)(word0 >> 24);
    if (have0) {
        printf("Option bytes cluster 0 (0x%08X) = 0x%08X\r\n",
               (unsigned)(info->ocden_addr & ~0x3u), (unsigned)word0);
        printf("  OCDEN (0x%08X) = 0x%02X\r\n", (unsigned)info->ocden_addr, ocden0);
    } else {
        printf("Option bytes cluster 0: unreadable (code flash inaccessible "
               "at the current protection level)\r\n");
    }

    // Cluster 1 (0x000001C0-C3) — the boot-swap mirror consulted INSTEAD
    // of cluster 0 whenever BTEN=0 ([UM] §5.1/§28.1).
    uint32_t word1 = 0;
    bool have1 = mem_read32(info->ocden_addr_swap & ~0x3u, &word1);
    uint8_t ocden1 = (uint8_t)(word1 >> 24);
    if (have1) {
        printf("Option bytes cluster 1 / boot-swap mirror (0x%08X) = 0x%08X\r\n",
               (unsigned)(info->ocden_addr_swap & ~0x3u), (unsigned)word1);
        printf("  OCDEN (0x%08X) = 0x%02X\r\n", (unsigned)info->ocden_addr_swap, ocden1);
    }

    // OCDM (data flash byte 0x500004) and BTEN (bit 0 of byte 0x500005) —
    // same 32-bit word, [UM] §28.3 fig. 28-4 / §5.1.
    uint32_t ocdm_word = 0;
    bool have_ocdm = mem_read32(info->ocdm_bten_addr, &ocdm_word);
    uint8_t ocdm = (uint8_t)(ocdm_word & 0xFF);
    bool bten = ((ocdm_word >> 8) & 1) != 0;
    if (have_ocdm) {
        printf("OCDM (0x%08X) = 0x%02X\r\n", (unsigned)info->ocdm_bten_addr, ocdm);
        printf("BTEN (0x%08X) = %u (boot-swap %s)\r\n",
               (unsigned)(info->ocdm_bten_addr + 1), bten,
               bten ? "disabled" : "ACTIVE -- cluster 1 governs, not cluster 0");
    } else {
        printf("OCDM/BTEN: unreadable (data flash inaccessible at the "
               "current protection level)\r\n");
    }

    // SWDIS — the third, runtime-only lock (DBGSTOPCR bit 24). 0 at reset;
    // this is what the "reset-release race" (SWD RACE) races against.
    uint32_t dbgstopcr = 0;
    bool have_dbg = mem_read32(info->dbgstopcr_addr, &dbgstopcr);
    if (have_dbg) {
        bool swdis = (dbgstopcr & BAT32_DBGSTOPCR_SWDIS) != 0;
        printf("DBGSTOPCR (0x%08X) = 0x%08X  SWDIS=%u (%s)\r\n",
               (unsigned)info->dbgstopcr_addr, (unsigned)dbgstopcr, swdis,
               swdis ? "SWD DISABLED by firmware" : "SWD enabled");
    }

    // Deduced protection level, [UM] §28.3 fig. 28-4 (doc §2.3). If BTEN
    // reads as active (0) and cluster 1 is readable, that cluster is the
    // one that actually governs — use it instead of cluster 0.
    if (have0 || have1) {
        bool use_swap = have_ocdm && !bten && have1;
        uint8_t ocden = use_swap ? ocden1 : ocden0;
        // Unknown OCDM never falsely reads as Level 2 (0x3C) — only a
        // confirmed match downgrades from "Level 1 assumed" to Level 2.
        uint8_t ocdm_for_level = have_ocdm ? ocdm : (uint8_t)(~BAT32_OCDM_LEVEL2);
        bat32_prot_level_t level = bat32_decode_level(ocden, ocdm_for_level);
        const char *level_str =
            level == BAT32_PROT_LEVEL0 ? "Level 0 (flash open)" :
            level == BAT32_PROT_LEVEL1 ? "Level 1 (chip-erase only)" :
                                          "Level 2 (no flash access via debugger)";
        printf("Deduced protection level: %s (from cluster %s, OCDEN=0x%02X%s)\r\n",
               level_str, use_swap ? "1 [boot-swap active]" : "0",
               ocden, have_ocdm ? "" : ", OCDM unknown");
        if (use_swap) {
            printf("  NOTE: boot-swap is ACTIVE -- fault/write target is "
                   "0x%08X, NOT 0x%08X\r\n",
                   (unsigned)info->ocden_addr_swap, (unsigned)info->ocden_addr);
        }
    } else {
        printf("Could not determine protection level (option bytes unreadable)\r\n");
    }

    return have0 || have1 || have_ocdm || have_dbg;
}

// --- STM32 flash operations ---

bool swd_stm32_flash_wait(const stm32_target_info_t *info, uint32_t timeout_ms) {
    uint32_t start = to_ms_since_boot(get_absolute_time());
    while (to_ms_since_boot(get_absolute_time()) - start < timeout_ms) {
        uint32_t sr;
        if (!mem_read32(info->flash_sr, &sr))
            return false;
        if (!(sr & 0x1))  // BSY bit
            return true;
        sleep_us(100);
    }
    return false;
}

bool swd_stm32_flash_unlock(const stm32_target_info_t *info) {
    // Clear sticky errors
    swd_write_dp(DP_ABORT, 0x1E);

    // Write key sequence to FLASH_KEYR
    if (!mem_write32(info->flash_keyr, info->flash_key1))
        return false;
    if (!mem_write32(info->flash_keyr, info->flash_key2))
        return false;

    // Verify unlock by reading CR — LOCK bit should be clear
    uint32_t cr;
    if (!mem_read32(info->flash_cr, &cr))
        return false;

    // F1/F3: LOCK is bit 7; L4: bit 31; F4: bit 31
    if (info->flash_base == 0x40022000)
        return !(cr & (1u << 7));    // F1/F3
    else
        return !(cr & (1u << 31));   // L4/F4
}

static bool stm32_opt_unlock(const stm32_target_info_t *info) {
    if (!swd_stm32_flash_unlock(info))
        return false;

    // Write option key sequence
    if (!mem_write32(info->flash_optkeyr, info->opt_key1))
        return false;
    if (!mem_write32(info->flash_optkeyr, info->opt_key2))
        return false;

    // Verify unlock
    uint32_t cr;
    if (!mem_read32(info->flash_cr, &cr))
        return false;

    // F1/F3: OPTWRE (bit 9) should be SET after unlock
    // L4/F4: OPTLOCK (bit 30) should be CLEAR after unlock
    if (info->flash_base == 0x40022000)
        return !!(cr & (1u << 9));    // F1/F3: OPTWRE set = unlocked
    else
        return !(cr & (1u << 30));    // L4/F4: OPTLOCK clear = unlocked
}

int swd_stm32_read_rdp(const stm32_target_info_t *info) {
    uint32_t optr;
    if (!mem_read32(info->flash_optr, &optr))
        return -1;

    uint8_t rdp_byte;

    // F1/F3: RDP is in FLASH_OBR register, bit 1 = RDPRT
    if (info->flash_base == 0x40022000 &&
        info->flash_optr == 0x4002201C) {
        // Try raw option bytes first (works at RDP0)
        uint32_t raw_opt;
        if (mem_read32(info->opt_base, &raw_opt)) {
            rdp_byte = raw_opt & 0xFF;
        } else {
            // Raw opt bytes unreadable (RDP1) — use OBR RDPRT flag
            swd_clear_errors();
            return (optr & (1u << 1)) ? 1 : 0;
        }
    }
    // F4: RDP in OPTCR bits [15:8]
    else if (info->flash_optr == 0x40023C14) {
        rdp_byte = (optr >> 8) & 0xFF;
    }
    // L4: RDP in OPTR bits [7:0]
    else {
        rdp_byte = optr & 0xFF;
    }

    if (rdp_byte == info->rdp_level0)
        return 0;
    if (rdp_byte == info->rdp_level2)
        return 2;
    return 1;
}

// Decode STM32L4 FLASH_OPTR register
static void decode_l4_optr(uint32_t optr) {
    uint8_t rdp = optr & 0xFF;
    printf("  RDP        = 0x%02X (Level %s)\r\n", rdp,
           rdp == 0xAA ? "0" : rdp == 0xCC ? "2 PERMANENT" : "1");

    uint8_t bor = (optr >> 8) & 0x7;
    const char *bor_str[] = {
        "~1.7V", "~2.0V", "~2.2V", "~2.5V",
        "~2.8V", "rsvd", "rsvd", "rsvd"
    };
    printf("  BOR_LEV    = %u (%s)\r\n", bor, bor_str[bor]);

    printf("  nRST_STOP  = %u (%s)\r\n", (optr >> 12) & 1,
           (optr >> 12) & 1 ? "no reset on Stop" : "reset on Stop");
    printf("  nRST_STDBY = %u (%s)\r\n", (optr >> 13) & 1,
           (optr >> 13) & 1 ? "no reset on Standby" : "reset on Standby");
    printf("  nRST_SHDW  = %u (%s)\r\n", (optr >> 14) & 1,
           (optr >> 14) & 1 ? "no reset on Shutdown" : "reset on Shutdown");
    printf("  IWDG_SW    = %u (%s)\r\n", (optr >> 16) & 1,
           (optr >> 16) & 1 ? "software IWDG" : "hardware IWDG");
    printf("  IWDG_STOP  = %u (%s)\r\n", (optr >> 17) & 1,
           (optr >> 17) & 1 ? "IWDG runs in Stop" : "IWDG frozen in Stop");
    printf("  IWDG_STDBY = %u (%s)\r\n", (optr >> 18) & 1,
           (optr >> 18) & 1 ? "IWDG runs in Standby" : "IWDG frozen in Standby");
    printf("  WWDG_SW    = %u (%s)\r\n", (optr >> 19) & 1,
           (optr >> 19) & 1 ? "software WWDG" : "hardware WWDG");
    printf("  BFB2       = %u (%s)\r\n", (optr >> 20) & 1,
           (optr >> 20) & 1 ? "boot from bank 2" : "boot from bank 1");
    printf("  DUALBANK   = %u (%s)\r\n", (optr >> 21) & 1,
           (optr >> 21) & 1 ? "dual-bank" : "single-bank");
    printf("  nBOOT1     = %u\r\n", (optr >> 23) & 1);
    printf("  SRAM2_PE   = %u (%s)\r\n", (optr >> 24) & 1,
           (optr >> 24) & 1 ? "SRAM2 parity check enabled" : "SRAM2 parity disabled");
    printf("  SRAM2_RST  = %u (%s)\r\n", (optr >> 25) & 1,
           (optr >> 25) & 1 ? "SRAM2 not erased on reset" : "SRAM2 erased on reset");
    printf("  nSWBOOT0   = %u (%s)\r\n", (optr >> 26) & 1,
           (optr >> 26) & 1 ? "BOOT0 from pin" : "BOOT0 from nBOOT0 bit");
    printf("  nBOOT0     = %u\r\n", (optr >> 27) & 1);
}

// Decode STM32F4 FLASH_OPTCR register
static void decode_f4_optcr(uint32_t optcr) {
    uint8_t rdp = (optcr >> 8) & 0xFF;
    printf("  RDP        = 0x%02X (Level %s)\r\n", rdp,
           rdp == 0xAA ? "0" : rdp == 0xCC ? "2 PERMANENT" : "1");

    uint8_t bor = (optcr >> 2) & 0x3;
    const char *bor_str[] = {"3 (~2.7V)", "2 (~2.4V)", "1 (~2.1V)", "0 (off)"};
    printf("  BOR_LEV    = %u (%s)\r\n", bor, bor_str[bor]);

    printf("  WDG_SW     = %u (%s)\r\n", (optcr >> 5) & 1,
           (optcr >> 5) & 1 ? "software WDG" : "hardware WDG");
    printf("  nRST_STOP  = %u\r\n", (optcr >> 6) & 1);
    printf("  nRST_STDBY = %u\r\n", (optcr >> 7) & 1);

    uint16_t nwrp = (optcr >> 16) & 0xFFF;
    printf("  nWRP       = 0x%03X (%s)\r\n", nwrp,
           nwrp == 0xFFF ? "no write protect" : "sectors protected");
}

// Decode STM32F1/F3 option bytes
static void decode_f1_options(uint32_t obr, uint32_t raw_opt) {
    uint8_t rdp = raw_opt & 0xFF;
    printf("  RDP        = 0x%02X (Level %s)\r\n", rdp,
           rdp == 0xA5 ? "0" : "1");
    printf("  RDPRT      = %u\r\n", (obr >> 1) & 1);
    printf("  WDG_SW     = %u (%s)\r\n", (obr >> 2) & 1,
           (obr >> 2) & 1 ? "software WDG" : "hardware WDG");
    printf("  nRST_STOP  = %u\r\n", (obr >> 3) & 1);
    printf("  nRST_STDBY = %u\r\n", (obr >> 4) & 1);

    uint8_t data0 = (raw_opt >> 16) & 0xFF;
    uint8_t data1 = (raw_opt >> 24) & 0xFF;
    printf("  Data0      = 0x%02X\r\n", data0);
    printf("  Data1      = 0x%02X\r\n", data1);
}

// Decode WRP register (L4)
static void decode_l4_wrp(const char *name, uint32_t val) {
    uint8_t start = val & 0xFF;
    uint8_t end = (val >> 16) & 0xFF;
    if (start > end)
        printf("  %s: disabled (start > end)\r\n", name);
    else
        printf("  %s: pages %u-%u protected\r\n", name, start, end);
}

bool swd_stm32_read_options(const stm32_target_info_t *info) {
    uint32_t optr;
    if (!mem_read32(info->flash_optr, &optr))
        return false;
    printf("FLASH_OPTR (0x%08X) = 0x%08X\r\n",
           (unsigned)info->flash_optr, (unsigned)optr);

    // L4: decode OPTR + WRP/PCROP
    if (info->flash_optr == 0x40022020) {
        decode_l4_optr(optr);

        uint32_t val;
        if (mem_read32(0x40022024, &val))
            printf("PCROP1SR = 0x%08X (start page %u)\r\n",
                   (unsigned)val, val & 0xFFFF);
        if (mem_read32(0x40022028, &val)) {
            printf("PCROP1ER = 0x%08X (end page %u, PCROP_RDP=%u)\r\n",
                   (unsigned)val, val & 0xFFFF, (val >> 31) & 1);
        }
        if (mem_read32(0x4002202C, &val)) {
            printf("WRP1AR   = 0x%08X\r\n", (unsigned)val);
            decode_l4_wrp("WRP1A", val);
        }
        if (mem_read32(0x40022030, &val)) {
            printf("WRP1BR   = 0x%08X\r\n", (unsigned)val);
            decode_l4_wrp("WRP1B", val);
        }
    }
    // F4: decode OPTCR
    else if (info->flash_optr == 0x40023C14) {
        decode_f4_optcr(optr);
    }
    // F1/F3: decode OBR + raw option bytes
    else if (info->flash_optr == 0x4002201C) {
        uint32_t raw_opt;
        if (mem_read32(info->opt_base, &raw_opt)) {
            decode_f1_options(optr, raw_opt);
        } else {
            // Raw opt bytes unreadable (RDP1) — decode from OBR only
            swd_clear_errors();
            printf("  RDPRT      = %u (%s)\r\n", (optr >> 1) & 1,
                   (optr >> 1) & 1 ? "RDP Level 1" : "RDP Level 0");
            printf("  WDG_SW     = %u (%s)\r\n", (optr >> 2) & 1,
                   (optr >> 2) & 1 ? "software WDG" : "hardware WDG");
            printf("  nRST_STOP  = %u\r\n", (optr >> 3) & 1);
            printf("  nRST_STDBY = %u\r\n", (optr >> 4) & 1);
            printf("  (raw option bytes not readable at RDP1)\r\n");
        }
    }

    return true;
}

bool swd_stm32_set_rdp(const stm32_target_info_t *info, uint8_t level) {
    uint8_t rdp_val;
    if (level == 0)
        rdp_val = info->rdp_level0;
    else if (level == 1)
        rdp_val = info->rdp_level1;
    else if (level == 2)
        rdp_val = info->rdp_level2;
    else
        return false;

    printf("[SWD] set_rdp: level=%u, rdp_val=0x%02X\r\n", level, rdp_val);

    // Generic path: halt, unlock, program option bytes.
    // NOTE: connect-under-reset was previously used here for F1, but under RDP1
    // the STM32F1 blocks flash unlock when debugger is detected at startup.
    // The generic halt-based approach works for all families including F1 under RDP1.
    swd_halt();
    sleep_ms(10);

    // Unlock flash + option bytes
    if (!stm32_opt_unlock(info)) {
        printf("[SWD] set_rdp: opt_unlock failed\r\n");
        return false;
    }

    // Wait for any ongoing operation
    if (!swd_stm32_flash_wait(info, 1000)) {
        printf("[SWD] set_rdp: flash_wait failed\r\n");
        return false;
    }

    // Family-specific option byte programming
    // F4: modify OPTCR register directly
    if (info->flash_optr == 0x40023C14) {
        uint32_t optcr;
        if (!mem_read32(info->flash_optr, &optcr))
            return false;
        optcr = (optcr & ~0xFF00) | ((uint32_t)rdp_val << 8);
        optcr |= (1 << 1);  // OPTSTRT
        if (!mem_write32(info->flash_optr, optcr))
            return false;
    }
    // L4: modify OPTR register, then fire OPTSTRT in CR
    else if (info->flash_optr == 0x40022020) {
        uint32_t optr;
        if (!mem_read32(info->flash_optr, &optr))
            return false;
        optr = (optr & ~0xFF) | rdp_val;
        if (!mem_write32(info->flash_optr, optr))
            return false;
        // Set OPTSTRT in CR
        uint32_t cr;
        if (!mem_read32(info->flash_cr, &cr))
            return false;
        cr |= (1 << 17);  // OPTSTRT
        if (!mem_write32(info->flash_cr, cr))
            return false;
    }
    // F1/F3: erase option bytes then reprogram via halfword writes
    else if (info->flash_optr == 0x4002201C) {
        uint32_t sr;

        // Clear any pending errors
        mem_write32(info->flash_sr, 0x34);

        // OPTER (erase option bytes) + STRT
        uint32_t cr;
        if (!mem_read32(info->flash_cr, &cr))
            return false;
        cr |= (1 << 5);  // OPTER
        if (!mem_write32(info->flash_cr, cr))
            return false;
        cr |= (1 << 6);  // STRT
        if (!mem_write32(info->flash_cr, cr))
            return false;

        if (!swd_stm32_flash_wait(info, 2000))
            return false;
        mem_read32(info->flash_sr, &sr);
        if (sr & 0x14) {
            printf("[SWD] F1: option erase failed (SR=0x%08X)\r\n", (unsigned)sr);
            return false;
        }

        // Clear OPTER, set OPTPG (option byte program)
        cr &= ~((1 << 5) | (1 << 6));
        cr |= (1 << 4);  // OPTPG
        if (!mem_write32(info->flash_cr, cr))
            return false;

        // Write RDP value as halfword to option byte base
        uint16_t opt_val = rdp_val | ((uint16_t)(~rdp_val & 0xFF) << 8);
        if (!mem_write16(info->opt_base, opt_val))
            return false;
        if (!swd_stm32_flash_wait(info, 1000))
            return false;

        // Program USER byte defaults (WDG_SW=1, nRST_STOP=1, nRST_STDBY=1)
        mem_write32(info->flash_sr, 0x34);  // Clear EOP
        if (!mem_write16(info->opt_base + 2, 0x00FF))
            return false;
        if (!swd_stm32_flash_wait(info, 1000))
            return false;

        // Clear OPTPG
        cr &= ~(1 << 4);
        if (!mem_write32(info->flash_cr, cr))
            return false;
    }

    // Wait for completion
    if (!swd_stm32_flash_wait(info, 5000))
        return false;

    // L4: launch option byte reload (triggers reset)
    if (info->flash_optr == 0x40022020) {
        uint32_t cr;
        if (!mem_read32(info->flash_cr, &cr))
            return false;
        cr |= (1 << 27);  // OBL_LAUNCH
        mem_write32(info->flash_cr, cr);  // Target will reset, may fail
    }

    // F1/F3: power cycle to apply option byte changes and reload shadow registers
    if (info->flash_optr == 0x4002201C) {
        extern void target_power_off(void);
        extern void target_power_on(void);

        printf("[SWD] F1: power cycling to apply option bytes...\r\n");
        swd_deinit();
        connected = false;
        ahb_initialized = false;

        target_power_off();
        sleep_ms(100);
        target_power_on();
        if (level == 0) {
            printf("[SWD] F1: waiting for mass erase...\r\n");
            sleep_ms(5000);
        } else {
            sleep_ms(500);
        }

        swd_init();
        if (swd_connect()) {
            int new_rdp = swd_stm32_read_rdp(info);
            printf("[SWD] F1: RDP level = %d\r\n", new_rdp);
            if (new_rdp == level) {
                printf("[SWD] F1: RDP change successful!\r\n");
                return true;
            }
        }
        printf("[SWD] F1: RDP readback failed\r\n");
        return false;
    }

    return true;
}

bool swd_stm32_flash_erase_page(const stm32_target_info_t *info, uint32_t page) {
    if (!swd_stm32_flash_unlock(info))
        return false;

    if (!swd_stm32_flash_wait(info, 1000))
        return false;

    // L4: PER | PNB | STRT
    if (info->flash_optr == 0x40022020) {
        uint32_t cr = (1 << 1)          // PER
                    | (page << 3)       // PNB[7:0] shifted to bits [10:3]
                    | (1 << 16);        // STRT
        if (!mem_write32(info->flash_cr, cr))
            return false;
    }
    // F1/F3: set PER, write page address to AR, set STRT
    else if (info->flash_optr == 0x4002201C) {
        uint32_t cr = (1 << 1);  // PER
        if (!mem_write32(info->flash_cr, cr))
            return false;
        // FLASH_AR is at flash_base + 0x14
        uint32_t page_addr = 0x08000000 + page * info->page_size;
        if (!mem_write32(info->flash_base + 0x14, page_addr))
            return false;
        cr |= (1 << 6);  // STRT
        if (!mem_write32(info->flash_cr, cr))
            return false;
    }
    // F4: sector erase via OPTCR — different model, use SNB field
    else {
        uint32_t cr = (page << 3)       // SNB
                    | (1 << 1)          // SER
                    | (1 << 16);        // STRT
        if (!mem_write32(info->flash_cr, cr))
            return false;
    }

    return swd_stm32_flash_wait(info, 5000);
}

uint32_t swd_stm32_flash_write(const stm32_target_info_t *info, uint32_t addr,
                                const uint8_t *data, uint32_t len) {
    if (!swd_stm32_flash_unlock(info))
        return 0;

    if (!swd_stm32_flash_wait(info, 1000))
        return 0;

    uint32_t written = 0;

    // L4: double-word (64-bit) programming
    if (info->flash_optr == 0x40022020) {
        // Set PG bit
        if (!mem_write32(info->flash_cr, (1 << 0)))  // PG
            return 0;

        // Write 8 bytes at a time (two 32-bit words)
        while (written + 8 <= len) {
            uint32_t w0 = data[written] | (data[written+1] << 8) |
                         (data[written+2] << 16) | (data[written+3] << 24);
            uint32_t w1 = data[written+4] | (data[written+5] << 8) |
                         (data[written+6] << 16) | (data[written+7] << 24);
            if (!mem_write32(addr + written, w0))
                break;
            if (!mem_write32(addr + written + 4, w1))
                break;
            if (!swd_stm32_flash_wait(info, 1000))
                break;
            written += 8;
        }

        // Clear PG bit
        mem_write32(info->flash_cr, 0);
    }
    // F1/F3: halfword (16-bit) programming — requires 16-bit bus access
    else if (info->flash_optr == 0x4002201C) {
        if (!mem_write32(info->flash_cr, (1 << 0)))  // PG
            return 0;

        while (written + 2 <= len) {
            uint16_t hw = data[written] | (data[written+1] << 8);
            if (!mem_write16(addr + written, hw))
                break;
            if (!swd_stm32_flash_wait(info, 1000))
                break;
            written += 2;
        }

        mem_write32(info->flash_cr, 0);
    }
    // F4: word (32-bit) programming with PSIZE
    else {
        // PG | PSIZE=2 (32-bit)
        if (!mem_write32(info->flash_cr, (1 << 0) | (2 << 8)))
            return 0;

        while (written + 4 <= len) {
            uint32_t w = data[written] | (data[written+1] << 8) |
                        (data[written+2] << 16) | (data[written+3] << 24);
            if (!mem_write32(addr + written, w))
                break;
            if (!swd_stm32_flash_wait(info, 1000))
                break;
            written += 4;
        }

        mem_write32(info->flash_cr, 0);
    }

    return written;
}
