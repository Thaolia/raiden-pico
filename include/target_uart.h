#ifndef TARGET_UART_H
#define TARGET_UART_H

#include <stdint.h>
#include <stdbool.h>
#include "config.h"

// Initialize target subsystem with defaults
void target_init(void);

// Set target type
void target_set_type(target_type_t type);

// Get target type
target_type_t target_get_type(void);

// Enter bootloader mode with optional baud rate and crystal speed
bool target_enter_bootloader(uint32_t baud, uint32_t crystal_khz);

// Initialize target UART (low-level)
void target_uart_init(uint8_t tx_pin, uint8_t rx_pin, uint32_t baud);

// Send single byte to target
void target_uart_send_byte(uint8_t byte);

// Send ASCII string to target (appends \r automatically)
void target_uart_send_string(const char *str);

// Send hex string to target (appends \r automatically)
void target_uart_send_hex(const char *hex_str);

// Process incoming data (call from main loop)
void target_uart_process(void);

// Get response count
uint16_t target_uart_get_response_count(void);

// Get response data
const char* target_uart_get_response(void);

// Clear response buffer
void target_uart_clear_response(void);

// Print response as hex
void target_uart_print_response_hex(void);

// Configure target reset
void target_reset_config(uint8_t pin, uint32_t period_ms, bool active_high);

// Execute target reset
void target_reset_execute(void);

// Check if initialized
bool target_is_initialized(void);

// Check if bootloader is synced
bool target_is_bl_synced(void);

// Debug mode control
void target_set_debug(bool enable);
bool target_get_debug(void);

// Transparent bridge timeout control (milliseconds)
void target_set_timeout(uint32_t timeout_ms);
uint32_t target_get_timeout(void);

// Target power control
void target_power_on(void);
void target_power_off(void);
void target_power_cycle(uint32_t time_ms);
bool target_power_get_state(void);
void target_power_ensure_on(void);  // silent power-on for connect/sync choke points

// Power mode (GP10/11/12 group: INTERNAL source vs EXTERNAL supply + crowbar gate)
void target_set_power_mode(power_mode_t mode);
power_mode_t target_get_power_mode(void);
void target_set_crowbar_polarity(bool active_high);
bool target_crowbar_gate_active_high(void);
void target_crowbar_gate_idle(void);  // drive GP11 to safe de-asserted idle (GPIO)
void target_power_sweep(void);
void target_power_glitch(float voltage, uint32_t count);
void target_bat32_glitch(float voltage, uint32_t count);
void target_bat32_glitch_sweep(void);

// Bounds for SWD GLITCH SWEEP (2D). The threshold axis is ADC counts, so it
// tops out at the 12-bit ADC full scale; the product is capped separately
// because a 2D grid multiplies.
#define SWD_GLITCH_SWEEP_MAX_DELAY_POINTS 200000u
#define SWD_GLITCH_SWEEP_MAX_THR_POINTS   4096u
#define SWD_GLITCH_SWEEP_MAX_SHOTS        1000u
#define SWD_GLITCH_SWEEP_MAX_TOTAL        2000000u
// Default per-shot rail-settle after a power-cycle in the SWD GLITCH path (ms).
// SETTLE 0 removes it -- e.g. for a power-on-synced dip in NORST mode. This is
// the glitch path's own knob; the global POWER_ON_SETTLE_MS (auto-power-on before
// connect/sync) is left untouched.
#define SWD_GLITCH_DEFAULT_SETTLE_MS      100u

// Reset-synchronised INTERNAL voltage glitch (BAT32G135): [power-cycle + settle] ->
// [assert nRST] -> [release] -> wait delay_us -> dip -> flash/SRAM oracle. With
// use_nrst the dip is timed to the nRST release (the option-byte / OCDEN load window).
//   power_cycle=false (NOPWR) : skip the per-shot power cycle, rail stays energised.
//   use_nrst=false   (NORST)  : never touch nRST; the dip is timed from the window
//                               opening, not from a reset edge (no reset-sync).
//   settle_ms                 : rail-settle after power-on within the power cycle
//                               (0 = none). Only used when power_cycle is true.
// On SUCCESS the target is left powered + connected for an immediate dump.
// INTERNAL power mode only.
void target_bat32_glitch_sync(uint32_t delay_us, float voltage,
                              uint32_t dwell_us, bool power_cycle, bool use_nrst,
                              uint32_t settle_ms);
// 2D sweep of target_bat32_glitch_sync over delay (outer) x dip-depth threshold in
// ADC counts (inner), `shots` attempts per cell. Stops on the first SUCCESS (target
// left powered + connected). Aborts on any pending CLI input; prints throttled
// progress + a final summary. Returns true iff a SUCCESS was found.
bool target_bat32_glitch_sync_sweep(uint32_t d_start_us, uint32_t d_end_us, uint32_t d_step_us,
                                    uint32_t thr_start, uint32_t thr_end, uint32_t thr_step,
                                    uint32_t shots, uint32_t dwell_us, bool power_cycle,
                                    bool use_nrst, uint32_t settle_ms);

// Reset-synchronised PIO-pulse glitch (BAT32G135): arms the PIO pulse engine on the
// nRST-release edge (GP15 -> GP3, TRIGGER GPIO RISING) and fires a sub-us pulse `pause`
// cycles (6.67 ns each) after release, `width` cycles wide, on GP2 (EMFI trigger, e.g.
// FaultyCat) AND GP11 (crowbar, in TARGET POWER EXT). Unlike the INTERNAL sag this pulse
// is invisible to the BAT32's LVD/POR (both need a >=300us excursion). Then runs the
// flash/SRAM oracle. Requires a GP15->GP3 strap and TARGET BAT32; for EMFI the injector
// (FaultyCat) must be armed separately -- raiden only makes the reset-synced trigger.
void target_bat32_glitch_pio(uint32_t pause_cy, uint32_t width_cy,
                             bool power_cycle, uint32_t settle_ms);
// 2D sweep of target_bat32_glitch_pio over pause (outer) x width (inner) in cycles,
// `shots` per cell. Stops on the first SUCCESS (target left connected). Aborts on any
// pending CLI input; prints throttled progress. Returns true iff a SUCCESS was found.
bool target_bat32_glitch_pio_sweep(uint32_t p_start, uint32_t p_end, uint32_t p_step,
                                   uint32_t w_start, uint32_t w_end, uint32_t w_step,
                                   uint32_t shots, bool power_cycle, uint32_t settle_ms);
// LPC CRP-bypass via ADC-controlled VDD glitch. Reads glitch config VMIN
// (depth, mV) and WIDTH (min-dwell, cycles → us). Each attempt: drop power
// to VMIN, dwell, restore, re-sync ISP, send R 0 4, classify response.
// Prints "[LPC GLITCH] vmin=...V bor=Y/N dur=...us isp=normal|bypass|effect|sync_fail"
// per attempt so a host sweep can parse per-shot results.
void target_power_lpc_glitch(uint32_t count);
void target_power_payload(float voltage, uint32_t max_attempts);
void target_power_bypass(uint32_t max_attempts, uint32_t dump_bytes);
void target_power_halt(uint32_t dump_bytes);
void target_power_literal(void);
void target_power_regdump(void);
void target_power_glitch_regdump(uint32_t max_attempts);
void target_power_resettest(void);

// STM32 USART bootloader commands (AN3155)
void stm32_bl_get(void);
void stm32_bl_get_version(void);
void stm32_bl_gid(void);
void stm32_bl_read(uint32_t addr, uint32_t count);
void stm32_bl_write(uint32_t addr, const uint8_t *data, uint32_t len);
void stm32_bl_go(uint32_t addr);
void stm32_bl_erase(int page, bool mass_erase);
void stm32_bl_readout_unprotect(void);
void stm32_bl_readout_protect(void);

// Breakpoint timing measurement via DWT cycle counter + ADC shunt current
// name_or_addr: breakpoint name or "0xADDR", NULL to list breakpoints
// samples: number of ADC samples (0 = default 4096)
// bootloader_mode: true = BOOT0=1 (bootloader), false = BOOT0=0 (flash)
void target_power_timing(const char *name_or_addr, uint32_t samples, bool bootloader_mode);

#endif // TARGET_UART_H
