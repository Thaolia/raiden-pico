/*
 * SWD (Serial Wire Debug) interface for Raiden-Pico
 *
 * Provides CLI-accessible SWD primitives for target debugging.
 */

#ifndef SWD_H
#define SWD_H

#include <stdint.h>
#include <stdbool.h>

// SWD Pin Configuration
#define SWD_SWCLK_PIN   17
#define SWD_SWDIO_PIN   18
#define SWD_NRST_PIN    15  // Target nRST (active low, active drive)

// SWD ACK responses
#define SWD_ACK_OK      0x1
#define SWD_ACK_WAIT    0x2
#define SWD_ACK_FAULT   0x4

// Debug Port (DP) register addresses (A[3:2])
#define DP_DPIDR        0x0     // Read: ID Register
#define DP_ABORT        0x0     // Write: Abort Register
#define DP_CTRL_STAT    0x4     // Control/Status
#define DP_SELECT       0x8     // AP Select
#define DP_RDBUFF       0xC     // Read Buffer (for AP reads)

// Common AP register addresses
#define AP_CSW          0x00    // Control/Status Word
#define AP_TAR          0x04    // Transfer Address Register
#define AP_DRW          0x0C    // Data Read/Write
#define AP_IDR          0xFC    // Identification Register

// Set/get SWD clock half-period delay in microseconds (0 = max speed).
// Only meaningful in SWD_PHY_BITBANG -- see swd_set_phy_mode() below for
// SWD_PHY_PIO's frequency, which is a separate knob.
void swd_set_speed(uint32_t delay_us);
uint32_t swd_get_speed(void);

// --- Physical layer selection (bit-bang GPIO vs. PIO2) ---
// See swd_phy.h for what the PIO layer buys: SWD_PHY_BITBANG's SPEED 0
// mis-samples on flying-wire benches (ACK=0x7, DPIDR shifted by one bit --
// see §0bis of TPLink_Tapo/07_BAT32G135_FAULTYCAT.md); SWD_PHY_PIO gives
// uniform, correctly-sampled edges fast enough for SWD RACE.
typedef enum {
    SWD_PHY_BITBANG = 0,
    SWD_PHY_PIO = 1,
} swd_phy_mode_t;

// Select the physical layer. For SWD_PHY_PIO, khz_if_pio is the SWCLK
// frequency to use; 0 reuses the last PIO frequency ever set (fails if none
// was ever set). Ignored for SWD_PHY_BITBANG. Forces a disconnect
// (equivalent to SWD DISCONNECT) if currently connected, since the pins'
// funcsel changes underneath the connection either way. Returns false
// without touching hardware if SWD_PHY_PIO is requested with no frequency
// available (khz_if_pio == 0 and none was ever set).
bool swd_set_phy_mode(swd_phy_mode_t mode, uint32_t khz_if_pio);
swd_phy_mode_t swd_get_phy_mode(void);
// SWD_PHY_PIO's current/last SWCLK frequency in kHz, 0 if never set.
uint32_t swd_get_phy_khz(void);

// Times a bare fast connect + AHB-AP bring-up + 2-word read at the current
// clk_delay_us or PIO frequency (whichever phy mode is active) -- the same
// sequence swd_race_once() runs, without touching nRST. Returns true and
// fills *us with the elapsed microseconds on success (DP connect and
// AHB-AP bring-up both succeeded); false (with *us left at the partial
// elapsed time, if non-NULL) if either step failed -- e.g. no target wired,
// or PIO SPEED set too high for this bench's wiring. Like every other SWD
// command, it leaves the target connected (debug domain powered, AHB-AP
// up) rather than disconnecting afterward -- it is a timing probe, not a
// read-only/side-effect-free one.
//
// *split (if non-NULL) breaks the total down by phase. This exists because
// the first optimisation attempt on this path targeted the wrong thing: a
// USB CDC printf was assumed to be the bulk of the frequency-independent
// ~1.3ms floor and removing it bought only ~7%. Measure the split before
// optimising anything here.
typedef struct {
    uint32_t connect_us;  // swd_connect_ex(true): line reset + JTAG-to-SWD + DPIDR
    uint32_t ahb_us;      // swd_init_ahb_ap_ex(true): power-up handshake + AP probe
    uint32_t read_us;     // swd_read_mem(0, .., 2): CSW + TAR + 2 x (DRW + RDBUFF)
} swd_bench_split_t;

bool swd_bench(uint32_t *us, swd_bench_split_t *split);

// Initialize SWD interface
void swd_init(void);

// Deinitialize SWD (pins to high-Z)
void swd_deinit(void);

// Connect to target (line reset + JTAG-to-SWD switch)
// Returns true on success
bool swd_connect(void);

// Connect to target, with the pin-settle delay and slow ADIv5.2
// dormant-state fallback skipped when fast=true. swd_connect() is
// swd_connect_ex(false). Used by the reset-release race primitives
// below, where every microsecond on the connect path counts.
bool swd_connect_ex(bool fast);

// Check if SWD is currently connected
bool swd_is_connected(void);

// Connect if not already connected (no-op if connected)
bool swd_ensure_connected(void);

// Connect under reset (hold nRST, connect, halt, release)
// Required for modifying option bytes on RDP-protected targets.
// Only works when the target's SW-DP still answers with nRST asserted —
// see swd_race_once() below for the inverse case.
bool swd_connect_under_reset(void);

// --- Reset-release race (RESETB vs SWD) ---
// See the block comment above swd_race_once() in swd.c for the rationale.

typedef enum {
    SWD_RACE_NO_DP = 0,   // SWD connect failed outright
    SWD_RACE_DP_ONLY,     // DP answered, but AHB-AP bring-up or the memory
                          // read that follows it failed
    SWD_RACE_MEM_BLOCKED, // memory read returned an all-0 / all-0xFF vector
                          // table (still protected)
    SWD_RACE_PERTURBED,   // memory read returned something, but it did not
                          // pass the SP/PC plausibility check (or no
                          // sram_size/flash_size bounds were supplied)
    SWD_RACE_SUCCESS,     // SP/PC look like a plausible Cortex-M reset vector
} swd_race_result_t;

typedef struct {
    swd_race_result_t result;
    uint32_t sp;
    uint32_t pc;
} swd_race_report_t;

#define SWD_RACE_SWEEP_MAX_POINTS 200000u
#define SWD_RACE_SWEEP_MAX_SHOTS  1000u

// One reset-release race attempt: assert nRST briefly, release, wait
// exactly delay_us, then race to connect + bring up the AHB-AP + read the
// target's reset vector (SP, PC) before its firmware can lock SWD.
// sram_base/size and flash_base/size gate the SUCCESS plausibility check
// (pass 0/0 to disable it — results still distinguish blocked vs.
// perturbed, just never classify SUCCESS). Returns true only on SUCCESS;
// *report (if non-NULL) is always filled in with the classification and
// the raw SP/PC read. On SUCCESS the target is left powered and SWD
// connected — the caller must dump before doing anything else (Level 0
// does not survive the next reset).
// "Persistent-AP" race: the short-sequence variant of swd_race_once().
//
// swd_race_once() re-establishes everything after releasing nRST (line
// reset + JTAG-to-SWD + DPIDR + AHB-AP bring-up) before it can read
// memory — ~250us at PIO 8MHz. That is far too slow for §8.3's *window A*
// (the hardware loading OCDEN/OCDM out of flash right after reset
// release), which is what gates flash access on a protected part.
//
// This variant bets that nRST resets the core and system but NOT the
// SW-DP / AHB-AP, which is the usual ARM behaviour. It pre-loads CSW and
// TAR before touching nRST, then after release + delay_us issues only the
// DRW reads. First flash access lands within a few microseconds of the
// reset edge instead of a few hundred.
//
// Fills *value with the word read at `addr` and *ack with the last ACK.
// Returns true only if the read actually completed (ACK OK). A false with
// *ack == SWD_ACK_FAULT is the "protection still on" answer; a false with
// no valid ACK means the DP did not survive the reset, which invalidates
// the premise rather than the target.
bool swd_race_persistent(uint32_t delay_us, uint32_t addr,
                          uint32_t *value, uint8_t *ack);

bool swd_race_once(uint32_t delay_us,
                    uint32_t sram_base, uint32_t sram_size,
                    uint32_t flash_base, uint32_t flash_size,
                    swd_race_report_t *report);

// Bounded sweep of swd_race_once() over [start_us, end_us] in steps of
// step_us, `shots` attempts per delay value. Stops immediately on the
// first SUCCESS (target left powered + connected, *success_out filled in
// if non-NULL) or when SWD_RACE_SWEEP_MAX_POINTS / _MAX_SHOTS would be
// exceeded (returns false without running). Aborts early on any pending
// CLI input. Prints throttled progress + a final summary via printf.
bool swd_race_sweep(uint32_t start_us, uint32_t end_us, uint32_t step_us,
                     uint32_t shots,
                     uint32_t sram_base, uint32_t sram_size,
                     uint32_t flash_base, uint32_t flash_size,
                     swd_race_report_t *success_out);

// Read Debug Port register
// addr: register address (0, 4, 8, or C)
// value: pointer to store result
// Returns true on ACK OK
bool swd_read_dp(uint8_t addr, uint32_t *value);

// Write Debug Port register
bool swd_write_dp(uint8_t addr, uint32_t value);

// Read Access Port register
// Handles SELECT register automatically
// ap: AP number (usually 0 for AHB-AP)
// addr: register address within AP
bool swd_read_ap(uint8_t ap, uint8_t addr, uint32_t *value);

// Enumerate the DAP: Access Ports + CoreSight ROM table (see swd.c). SWD-only;
// no JTAG required. Auto-connect is handled by the SWD command dispatcher.
void swd_scan(void);

// Diffable capture of non-flash observable state (core regs + SCB fault status +
// key peripherals + SRAM window). Fault-tolerant per read (works at RDP0/RDP1).
void swd_snapshot(uint32_t sram_addr, uint32_t sram_len);

// Atomic flash-read-leak probe: MEM-AP read of addr + capture of raw data phase /
// RDBUFF / sticky state with no intervening error-clear. See RDP1_DEBUG_MATRIX.md.
void swd_leakprobe(uint32_t addr);

// Write Access Port register
bool swd_write_ap(uint8_t ap, uint8_t addr, uint32_t value);

// Read memory via AHB-AP
// addr: target memory address
// data: buffer to store data
// count: number of 32-bit words to read
// Returns number of words successfully read
uint32_t swd_read_mem(uint32_t addr, uint32_t *data, uint32_t count);

// Write memory via AHB-AP
// Returns number of words successfully written
uint32_t swd_write_mem(uint32_t addr, const uint32_t *data, uint32_t count);

// Get last ACK response
uint8_t swd_get_last_ack(void);

// Clear any fault condition
bool swd_clear_errors(void);

// Connect and read DPIDR (convenience function)
// Returns DPIDR on success, 0 on failure
uint32_t swd_identify(void);

// --- Cortex-M debug registers ---
#define DHCSR       0xE000EDF0  // Debug Halting Control/Status
#define DCRSR       0xE000EDF4  // Debug Core Register Selector
#define DCRDR       0xE000EDF8  // Debug Core Register Data
#define DEMCR       0xE000EDFC  // Debug Exception/Monitor Control
#define CPUID       0xE000ED00  // CPUID Base Register
#define AIRCR       0xE000ED0C  // Application Interrupt/Reset Control
#define CFSR        0xE000ED28  // Configurable Fault Status
#define HFSR        0xE000ED2C  // HardFault Status

// DHCSR key for writes
#define DBGKEY      0xA05F0000

// FPB (Flash Patch and Breakpoint) registers
#define FP_CTRL     0xE0002000  // FPB Control Register
#define FP_REMAP    0xE0002004  // FPB Remap Register
#define FP_COMP0    0xE0002008  // FPB Comparator 0
// FP_COMPn = FP_COMP0 + n*4, n=0..5 (instruction), 6..7 (literal)

// DWT (Data Watchpoint and Trace) registers
#define DWT_CTRL    0xE0001000  // DWT Control
#define DWT_CYCCNT  0xE0001004  // DWT Cycle Counter

// Cortex-M debug ID register (STM32-specific)
#define DBG_IDCODE  0xE0042000

// Assert nRST (hold target in reset)
void swd_nrst_assert(void);

// Release nRST (let target run)
void swd_nrst_release(void);

// Pulse nRST low for given duration in ms, then release
void swd_nrst_pulse(uint32_t ms);

// Halt target core
bool swd_halt(void);

// Resume target core
bool swd_resume(void);

// Read a CPU register (r0-r15, xPSR etc) while halted
// reg: 0-15 = r0-r15, 16 = xPSR, 17 = MSP, 18 = PSP
bool swd_read_core_reg(uint8_t reg, uint32_t *value);

// Write a CPU register (r0-r15, xPSR etc) while halted
bool swd_write_core_reg(uint8_t reg, uint32_t value);

// Detect target: read CPUID and STM32 debug ID code
// Fills cpuid and dbg_idcode, returns true on success
bool swd_detect(uint32_t *cpuid, uint32_t *dbg_idcode);

// --- BAT32G135 high-level operations (read-only) ---

#include "bat32_target.h"

// Read + decode option bytes (OCDEN/OCDM/BTEN, both clusters) and
// DBGSTOPCR.SWDIS; prints the deduced protection level.
bool swd_bat32_read_options(const bat32_target_info_t *info);

// --- BAT32G135 flash writes (DESTRUCTIVE) ---
// Transcribed from Cmsemicon's own Driver/src/flash.c (official CMSIS
// pack) — see the block comment in swd.c, which also lists where that
// source corrects 07_BAT32G135_FAULTYCAT.md §7.4.
//
// Both halt the core before touching the flash controller (the vendor
// driver runs from RAM with interrupts off for the same reason) and
// re-lock FLPROT afterwards, including on failure.

// Program `len` bytes at `addr`. Granularity is a BYTE — programming can
// only clear bits (1 -> 0); a byte needing any 0 -> 1 requires an erase
// first and will fail verification. No erase is performed here. Sector
// erase is deliberately not offered at all: OCDEN sits in sector 0
// alongside the reset vector table.
bool swd_bat32_flash_program(uint32_t addr, const uint8_t *data, uint32_t len);

// Read `words` words of flash at `src` THROUGH THE CORE, bypassing the
// debugger-side flash block that protection Level 1 applies. Injects a
// 16-byte Thumb copier into SRAM, points the core at it, and reads the
// result back out of SRAM — all three premises (SRAM readable at L1, core
// haltable at L1, core allowed to read flash) are measured; see the block
// comment in swd.c. Max 1024 words (4 KB) per call.
//
// ⚠ UNTESTED end to end. Prints which premise failed rather than guessing:
// SRAM not writable, register writes refused, or the core never reaching
// the BKPT. Harmless at Level 0 too, where it doubles as its own test — it
// must return exactly what a plain swd_read_mem() returns.
bool swd_bat32_ram_read(uint32_t src, uint32_t words, uint32_t *out);

// Full chip erase (FLERMD=0x08). The only erase the BAT32 permits at
// protection Level 1, and the only documented way back from it — but
// whether it is reachable over SWD once protected is NOT documented.
// Erases code flash AND data flash: everything.
bool swd_bat32_chip_erase(void);

// Sector erase (vendor FLERMD=0x10). The ONLY way to blank the data flash:
// the chip erase does not touch that array. `addr` is any address inside the
// sector to erase.
bool swd_bat32_sector_erase(uint32_t addr);

// --- STM32 high-level operations (require target type set) ---

#include "config.h"

// Read RDP level from flash option register
// Returns 0, 1, or 2 for the level, or -1 on error
int swd_stm32_read_rdp(const stm32_target_info_t *info);

// Read option bytes (prints all relevant option registers)
bool swd_stm32_read_options(const stm32_target_info_t *info);

// Set RDP level (0 = unprotect + mass erase, 1 = protect)
// WARNING: Setting RDP to 0 triggers mass erase on most families
bool swd_stm32_set_rdp(const stm32_target_info_t *info, uint8_t level);

// Unlock flash controller
bool swd_stm32_flash_unlock(const stm32_target_info_t *info);

// Erase a flash page (page number, not address)
bool swd_stm32_flash_erase_page(const stm32_target_info_t *info, uint32_t page);

// Write flash (must be erased first, handles family-specific write size)
// Returns number of bytes written
uint32_t swd_stm32_flash_write(const stm32_target_info_t *info, uint32_t addr,
                                const uint8_t *data, uint32_t len);

// Wait for flash BSY flag to clear
bool swd_stm32_flash_wait(const stm32_target_info_t *info, uint32_t timeout_ms);

#endif // SWD_H
