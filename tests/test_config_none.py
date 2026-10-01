"""Config NONE: Tests requiring no external hardware - just the Pico2 over USB.

Wiring: None. Only USB connection to Raiden Pico required.
"""

import re

import pytest


def _glitch_count(raiden):
    """Parse 'Glitch Count: N' out of STATUS (USB-only, no scope)."""
    m = re.search(r"Glitch Count:\s*(\d+)", raiden.cmd("STATUS", wait=2))
    assert m, "STATUS is missing the 'Glitch Count:' line"
    return int(m.group(1))


def _console_owns_uart0(raiden):
    """True when the firmware was built with -DRAIDEN_CONSOLE_UART=ON.

    UART0/GP0/GP1 then carries the CLI console instead of the ChipSHOUTER, and
    every CS command answers with an explicit unavailability error rather than
    its usual argument validation. VERSION is the only way to tell from here.
    """
    return "ChipSHOUTER disabled" in raiden.cmd("VERSION")


def _is_armed(resp):
    """ARM query prints 'ARMED' or 'DISARMED' — careful, ARMED is a substring of DISARMED."""
    return "ARMED" in resp and "DISARMED" not in resp


# ── Help / Version / Status ──────────────────────────────────

class TestSystemInfo:

    def test_help_has_sections(self, raiden):
        """Check HELP output contains key sections.

        Note: HELP output is ~4KB which can overflow USB CDC TX buffer.
        We check sections reliably received in the first ~3KB.
        """
        r = raiden.cmd("HELP", wait=5)
        for section in ["ChipSHOUTER", "Glitch", "Target", "Trigger", "Trace", "SWD"]:
            assert section in r, f"HELP missing section: {section}"

    def test_version(self, raiden):
        r = raiden.cmd("VERSION")
        assert "Raiden Pico" in r

    def test_version_reports_console_transport(self, raiden):
        """VERSION must say which link carries the CLI.

        UART0/GP0/GP1 is owned either by the ChipSHOUTER or by the console
        (build option RAIDEN_CONSOLE_UART) — never both. The VERSION readout is
        the only way the host can tell which binary is actually on the chip.
        """
        r = raiden.cmd("VERSION")
        assert "Console:" in r, "VERSION must report the console transport"
        assert ("UART0" in r) or ("USB CDC only" in r)

    def test_cs_matches_console_transport(self, raiden):
        """CS is available iff the console does NOT own UART0.

        Both branches are asserted, so this test is meaningful against either
        build instead of being skipped on one of them.
        """
        console_owns_uart0 = "ChipSHOUTER disabled" in raiden.cmd("VERSION")
        r = raiden.cmd("CS STATUS", wait=3)
        if console_owns_uart0:
            assert "ERROR" in r and "UART0" in r, (
                "CS must refuse explicitly when the console owns UART0, "
                "never silently no-op"
            )
        else:
            assert "CS unavailable" not in r

    def test_pins_names_the_uart0_owner(self, raiden):
        """PINS must show who owns GP0/GP1 in this build (pins skill, rule 2)."""
        r = raiden.cmd("PINS", wait=2)
        assert "GP0" in r
        assert ("ChipSHOUTER UART TX" in r) or ("CLI console UART0 TX" in r), (
            "PINS must name the current owner of GP0/GP1"
        )

    def test_status_fields(self, raiden):
        r = raiden.cmd("STATUS", wait=2)
        for field in ["RP2350", "Armed", "Pause", "Width", "Gap", "Count", "Trigger", "Target"]:
            assert field in r, f"STATUS missing field: {field}"

    def test_pins_output(self, raiden):
        r = raiden.cmd("PINS", wait=2)
        for pin in ["GP2", "GP7", "GP15", "GP17", "GP18"]:
            assert pin in r, f"PINS missing: {pin}"
        # Status LED is board-aware: GP25 on Pico 2, on the CYW43 (no GPIO) on
        # a Pico 2 W build.
        assert "GP25" in r or "CYW43" in r or "wireless" in r.lower(), \
            "PINS missing status-LED line"
        assert "Glitch Output (normal)" in r
        assert "Glitch Output (inverted)" in r

    def test_unknown_command(self, raiden):
        r = raiden.cmd("FOOBAR")
        assert "ERROR" in r
        assert "Unknown" in r or "HELP" in r


# ── SET / GET parameters ─────────────────────────────────────

class TestGlitchParams:

    def test_set_get_pause(self, raiden):
        raiden.cmd("SET PAUSE 1000")
        r = raiden.cmd("GET PAUSE")
        assert "1000" in r

    def test_set_get_width(self, raiden):
        raiden.cmd("SET WIDTH 150")
        r = raiden.cmd("GET WIDTH")
        assert "150" in r

    def test_set_get_gap(self, raiden):
        raiden.cmd("SET GAP 200")
        r = raiden.cmd("GET GAP")
        assert "200" in r

    def test_set_get_count(self, raiden):
        raiden.cmd("SET COUNT 5")
        r = raiden.cmd("GET COUNT")
        assert "5" in r

    def test_get_all(self, raiden):
        r = raiden.cmd("GET")
        for param in ["PAUSE", "WIDTH", "GAP", "COUNT"]:
            assert param in r, f"GET missing: {param}"

    def test_set_all(self, raiden):
        r = raiden.cmd("SET")
        for param in ["PAUSE", "WIDTH", "GAP", "COUNT"]:
            assert param in r, f"SET missing: {param}"

    def test_set_unknown_param(self, raiden):
        r = raiden.cmd("SET FOOBAR 100")
        assert "ERROR" in r

    def test_get_unknown_param(self, raiden):
        r = raiden.cmd("GET FOOBAR")
        assert "ERROR" in r


# ── Trigger configuration ────────────────────────────────────

class TestTrigger:

    def test_trigger_none(self, raiden):
        r = raiden.cmd("TRIGGER NONE")
        assert "OK" in r or "disabled" in r.lower()

    def test_trigger_show(self, raiden):
        raiden.cmd("TRIGGER NONE")
        r = raiden.cmd("TRIGGER")
        assert "NONE" in r

    def test_trigger_gpio_rising(self, raiden):
        raiden.cmd("TRIGGER GPIO RISING")
        r = raiden.cmd("TRIGGER")
        assert "GPIO" in r
        assert "RISING" in r

    def test_trigger_gpio_falling(self, raiden):
        raiden.cmd("TRIGGER GPIO FALLING")
        r = raiden.cmd("TRIGGER")
        assert "GPIO" in r
        assert "FALLING" in r

    def test_trigger_uart_byte(self, raiden):
        raiden.cmd("TRIGGER UART 3F")
        r = raiden.cmd("TRIGGER")
        assert "UART" in r
        assert "3F" in r.upper()

    def test_trigger_uart_tx(self, raiden):
        raiden.cmd("TRIGGER UART 0D TX")
        r = raiden.cmd("TRIGGER")
        assert "TX" in r

    def test_trigger_uart_rx(self, raiden):
        raiden.cmd("TRIGGER UART 0A RX")
        r = raiden.cmd("TRIGGER")
        assert "RX" in r

    def test_trigger_cleanup(self, raiden):
        raiden.cmd("TRIGGER NONE")


# ── Arm / Disarm ─────────────────────────────────────────────

class TestArm:

    def test_arm_disarm_cycle(self, raiden):
        raiden.cmd("TRIGGER GPIO RISING")  # Need trigger configured to arm

        r = raiden.cmd("ARM ON")
        assert "Armed" in r or "OK" in r

        r = raiden.cmd("ARM")
        assert "ARMED" in r.upper()

        r = raiden.cmd("ARM OFF")
        assert "Disarmed" in r or "OK" in r

        raiden.cmd("TRIGGER NONE")

    def test_arm_off_when_disarmed(self, raiden):
        r = raiden.cmd("ARM OFF")
        # Should not error
        assert "ERROR" not in r

    def test_arm_trace_mode(self, raiden):
        raiden.cmd("TRIGGER GPIO RISING")
        r = raiden.cmd("ARM TRACE")
        assert "OK" in r or "armed" in r.lower() or "Trace" in r
        raiden.cmd("ARM OFF")
        raiden.cmd("TRIGGER NONE")

    def test_arm_bad_arg(self, raiden):
        r = raiden.cmd("ARM FOOBAR")
        assert "ERROR" in r


# ── Clock generator ──────────────────────────────────────────

class TestClock:

    def test_clock_set_and_on(self, raiden):
        r = raiden.cmd("CLOCK 12000000 ON")
        assert "12" in r or "OK" in r

    def test_clock_off(self, raiden):
        r = raiden.cmd("CLOCK OFF")
        assert "OK" in r or "OFF" in r or "stopped" in r.lower()

    def test_clock_query(self, raiden):
        raiden.cmd("CLOCK 8000000 ON")
        r = raiden.cmd("CLOCK")
        assert "8000000" in r or "8.0" in r or "8MHz" in r
        raiden.cmd("CLOCK OFF")

    def test_clock_invalid(self, raiden):
        r = raiden.cmd("CLOCK FOOBAR")
        assert "ERROR" in r


# ── Debug mode ───────────────────────────────────────────────

class TestDebug:

    def test_debug_on_off(self, raiden):
        r = raiden.cmd("DEBUG ON")
        assert "ON" in r

        r = raiden.cmd("DEBUG OFF")
        assert "OFF" in r

    def test_debug_query(self, raiden):
        raiden.cmd("DEBUG OFF")
        r = raiden.cmd("DEBUG")
        assert "OFF" in r

    def test_swd_scan_recognized(self, raiden):
        """SWD SCAN must be a recognised sub-command (registered in the matcher).
        With a target wired it enumerates the DAP; with none it errors on connect
        — either way it must not be rejected as an unknown sub-command."""
        r = raiden.cmd("SWD SCAN", wait=3)
        assert "Unknown SWD" not in r
        assert ("SCAN complete" in r or "Access Ports" in r or
                "connection failed" in r or "DPIDR" in r)

    def test_swd_snapshot_bad_arg_errors(self, raiden):
        """SWD SNAPSHOT rejects a bad sram_addr. (SWD auto-connect runs first, so a
        target-less setup sees a connect error instead — both are errors.)"""
        r = raiden.cmd("SWD SNAPSHOT notanumber", wait=2)
        assert "ERROR" in r and ("sram_addr" in r or "onnection" in r)

    def test_swd_snapshot_recognized(self, raiden):
        """SWD SNAPSHOT must be a recognised sub-command."""
        r = raiden.cmd("SWD SNAPSHOT", wait=3)
        assert "Unknown SWD" not in r
        assert ("SNAPSHOT" in r or "REG." in r or "connection failed" in r or "DPIDR" in r)

    def test_swd_leakprobe_usage_errors(self, raiden):
        """SWD LEAKPROBE needs an address. (SWD auto-connect runs first, so a
        target-less setup errors on connect instead — both are errors.)"""
        r = raiden.cmd("SWD LEAKPROBE", wait=2)
        assert "ERROR" in r and ("LEAKPROBE" in r or "onnection" in r)


# ── Target type ──────────────────────────────────────────────

class TestTargetType:

    @pytest.mark.parametrize("target", ["LPC", "STM32F1", "STM32F3", "STM32F4", "STM32L4", "BAT32"])
    def test_set_target_type(self, raiden, target):
        r = raiden.cmd(f"TARGET {target}")
        assert "OK" in r or target in r

    def test_target_type_in_status(self, raiden):
        raiden.cmd("TARGET STM32F1")
        r = raiden.cmd("STATUS", wait=2)
        assert "STM32F1" in r

    def test_target_type_bat32_in_status(self, raiden):
        raiden.cmd("TARGET BAT32")
        r = raiden.cmd("STATUS", wait=2)
        assert "BAT32" in r
        raiden.cmd("TARGET STM32F1")  # restore a benign default for later tests

    def test_target_timeout_roundtrip(self, raiden):
        raiden.cmd("TARGET TIMEOUT 100")
        r = raiden.cmd("TARGET TIMEOUT")
        assert "100" in r

    def test_target_reset_no_target(self, raiden):
        """TARGET RESET toggles GP15, safe without target."""
        r = raiden.cmd("TARGET RESET")
        assert "ERROR" not in r

    def test_target_power_toggle(self, raiden):
        r = raiden.cmd("TARGET POWER ON")
        assert "ERROR" not in r

        r = raiden.cmd("TARGET POWER OFF")
        assert "ERROR" not in r

        raiden.cmd("TARGET POWER ON")  # restore

    def test_target_power_cycle(self, raiden):
        r = raiden.cmd("TARGET POWER CYCLE 100", wait=1.5)
        assert "ERROR" not in r

    def test_target_bad_subcommand(self, raiden):
        r = raiden.cmd("TARGET FOOBAR")
        assert "ERROR" in r

    def test_target_bat99_unknown_rejected(self, raiden):
        # Not a valid target and not an unambiguous prefix of BAT32 or
        # anything else — must error, not silently no-op.
        r = raiden.cmd("TARGET BAT99")
        assert "ERROR" in r

    def test_target_bat32_swd_opt_no_wiring_fails_at_connect(self, raiden):
        """No target is wired in config_none, so SWD OPT/RDP/FLASH under
        TARGET BAT32 fail at the auto-connect stage (same as they would
        for any other target type) rather than reaching the BAT32-specific
        branches — this only confirms the command fails cleanly, not
        silently. The BAT32-specific refusal text (RDP register / FLASH
        read-only) needs a live SW-DP to reach and is covered under
        --config=swd in test_config_swd.py.
        """
        raiden.cmd("TARGET BAT32")
        assert "ERROR" in raiden.cmd("SWD OPT", wait=2)
        assert "ERROR" in raiden.cmd("SWD RDP", wait=2)
        assert "ERROR" in raiden.cmd("SWD FLASH ERASE 0", wait=2)
        raiden.cmd("TARGET STM32F1")  # restore a benign default


# ── UART1 switching (Target <-> GRBL bleed regression) ───────
#
# UART1 is shared between the Target (GP4/5) and GRBL (GP8/9). Regression for the
# TTL bleed where a TARGET command after a GRBL command wrote to UART1 while it
# was still routed to GP8/9 (bleeding bootloader traffic onto the GRBL
# controller). The fix makes target TX auto-reclaim UART1 from GRBL. No target
# or GRBL controller is attached — this only observes the pin-routing handover.

class TestUartSwitching:

    def test_target_send_reclaims_uart_from_grbl(self, raiden):
        raiden.cmd("GRBL POS", wait=3)     # inits GRBL UART on GP8/9 (grbl active)
        raiden.cmd("TARGET STM32F1")       # valid target so SEND is accepted
        r = raiden.cmd("TARGET SEND 7F", wait=2)
        assert "reclaimed from GRBL" in r  # auto-switched back to GP4/5
        assert "ERROR" not in r

    def test_grbl_still_works_after_target(self, raiden):
        """The reverse direction stays clean: a GRBL command after a target
        command re-inits the GRBL UART (grbl_init deinits GP4/5)."""
        raiden.cmd("TARGET SEND 7F", wait=1)   # target owns UART1
        r = raiden.cmd("GRBL POS", wait=3)      # must re-init GRBL on GP8/9
        assert "Grbl UART initialized" in r or "GP8" in r


# ── BYPASS per-family payload selection (error path only) ────
#
# Only the unsupported-family ERROR path is exercised here: it returns from the
# family gate BEFORE the sweep/POR glitch, so it drives no hardware and is safe
# under config_none. The F1/F4 happy path fires the power glitch and needs a
# wired target — that's power-int gated bench validation, not here.

class TestBypassPayloadFamily:

    def test_bypass_unsupported_family_errors(self, raiden):
        """A family with no ported BYPASS payload (e.g. STM32L4) must error at
        the family gate, before any sweep or glitch."""
        raiden.cmd("TARGET POWER INT")   # ensure not EXTERNAL (default anyway)
        raiden.cmd("TARGET STM32L4")
        r = raiden.cmd("TARGET GLITCH BYPASS", wait=3)
        assert "ERROR" in r
        assert "No BYPASS payload" in r
        assert "STM32F1" in r and "STM32F4" in r  # names the supported families

    def test_bypass_bad_voltage_errors(self, raiden):
        """The optional [voltage_mv] arg must reject non-numeric / out-of-range
        input at parse time, before any hardware access."""
        r = raiden.cmd("TARGET GLITCH BYPASS 5 0 notanumber", wait=1)
        assert "ERROR" in r and "voltage_mv" in r
        r = raiden.cmd("TARGET GLITCH BYPASS 5 0 9999", wait=1)  # > 3300 mV
        assert "ERROR" in r and "voltage_mv" in r

    def test_shadowbypass_bad_voltage_errors(self, raiden):
        """SHADOWBYPASS shares the same [voltage_mv] parse/range guard."""
        r = raiden.cmd("TARGET GLITCH SHADOWBYPASS 1 64 notanumber", wait=1)
        assert "ERROR" in r and "voltage_mv" in r
        r = raiden.cmd("TARGET GLITCH SHADOWBYPASS 1 64 9999", wait=1)  # > 3300 mV
        assert "ERROR" in r and "voltage_mv" in r


# ── External PSU command (error/parse paths only) ────────────
#
# These stay on the safe paths that return BEFORE the PSU UART claims GP10/11:
# unknown sub-command, missing/out-of-range args, and the power-ON exclusion
# (which refuses before retasking any pin). Actual PSU comms need the wired
# TENMA + MAX3232 and are bench-tested.

class TestPsu:

    def test_psu_usage_no_arg(self, raiden):
        r = raiden.cmd("PSU")
        assert "PSU" in r and ("VOLT" in r or "Usage" in r)
        assert "ERROR" not in r  # bare PSU prints usage/state, not an error

    def test_psu_unknown_subcommand_errors(self, raiden):
        r = raiden.cmd("PSU FOOBAR")
        assert "ERROR" in r

    def test_psu_volt_missing_arg_errors(self, raiden):
        r = raiden.cmd("PSU VOLT")
        assert "ERROR" in r

    def test_psu_volt_out_of_range_errors(self, raiden):
        r = raiden.cmd("PSU VOLT 99999")
        assert "ERROR" in r
        assert "range" in r.lower()

    def test_psu_curr_out_of_range_errors(self, raiden):
        r = raiden.cmd("PSU CURR 99999")
        assert "ERROR" in r
        assert "range" in r.lower()

    def test_psu_refused_while_target_power_on(self, raiden):
        """With the target power group ON, a PSU command must refuse (shared
        GP10/11) before retasking any pin."""
        raiden.cmd("TARGET POWER INT")
        raiden.cmd("TARGET POWER ON")
        try:
            r = raiden.cmd("PSU ID", wait=1)
            assert "ERROR" in r
            assert "power is ON" in r or "TARGET POWER OFF" in r
        finally:
            raiden.cmd("TARGET POWER OFF")


# ── Glitch execution ─────────────────────────────────────────

class TestGlitch:

    def test_glitch_execute(self, raiden):
        r = raiden.cmd("GLITCH")
        # May fail (no trigger) or succeed, but should not crash
        assert "Glitch" in r or "ERROR" in r

    def test_glitch_requires_arm(self, raiden):
        """GLITCH while disarmed must error, not silently no-op."""
        raiden.cmd("TRIGGER NONE")
        raiden.cmd("ARM OFF")
        r = raiden.cmd("GLITCH")
        assert "ERROR" in r or "Failed" in r

    def test_glitch_increments_count(self, raiden):
        """A successful manual GLITCH bumps the glitch count by exactly 1 (USB-verifiable)."""
        raiden.cmd("TRIGGER NONE")
        before = _glitch_count(raiden)
        assert "armed" in raiden.cmd("ARM ON").lower()
        assert "Glitch executed" in raiden.cmd("GLITCH")
        assert _glitch_count(raiden) == before + 1

    def test_glitch_auto_disarms(self, raiden):
        """Manual GLITCH auto-disarms (soft disarm): ARM reports DISARMED afterwards."""
        raiden.cmd("TRIGGER NONE")
        raiden.cmd("ARM ON")
        assert _is_armed(raiden.cmd("ARM"))          # armed before firing
        raiden.cmd("GLITCH")
        assert "DISARMED" in raiden.cmd("ARM")       # auto-disarmed after firing
        raiden.cmd("ARM OFF")

    def test_reset_command(self, raiden):
        r = raiden.cmd("RESET")
        assert "ERROR" not in r


# ── Auto power-on at the connect/sync choke points ───────────

@pytest.mark.config_power_int
class TestAutoPowerOn:
    """SWD CONNECT and TARGET SYNC energise the target first, so a power-off boot
    default (or an explicit POWER OFF) doesn't leave them connecting to a dead
    target. USB-only: we assert power flips back ON even though no target is wired
    (the connect itself fails, which is fine — we only check it powered on first).

    GATED config_power_int: this drives the INTERNAL GP10/11/12 power group
    (POWER OFF then auto-ON), so it needs --config=power-int (confirm INTERNAL/
    ganged wiring, or no target). It does not run under the default config_none."""

    def test_swd_connect_auto_powers_target(self, raiden):
        raiden.cmd("TARGET POWER INT")
        raiden.cmd("TARGET POWER OFF")
        assert "(GP10/11/12): OFF" in raiden.cmd("TARGET POWER")
        raiden.cmd("SWD CONNECT", wait=2)        # no target -> connect fails, but powers on first
        assert "(GP10/11/12): ON" in raiden.cmd("TARGET POWER")
        raiden.cmd("SWD DISCONNECT")

    def test_sync_auto_powers_target(self, raiden):
        raiden.cmd("TARGET LPC")
        raiden.cmd("TARGET POWER INT")
        raiden.cmd("TARGET POWER OFF")
        assert "(GP10/11/12): OFF" in raiden.cmd("TARGET POWER")
        raiden.cmd("TARGET SYNC 115200 12000 50 1", wait=4)  # no target -> sync fails, powers on first
        assert "(GP10/11/12): ON" in raiden.cmd("TARGET POWER")


# ── API mode ─────────────────────────────────────────────────

class TestAPIMode:

    def test_api_on_off(self, raiden):
        r = raiden.cmd("API ON")
        assert "ON" in r or "+" in r

        r = raiden.cmd("API OFF")
        # After API OFF, response is in normal mode
        assert "ERROR" not in r

    def test_api_protocol(self, raiden):
        raiden.cmd("API ON")
        r = raiden.cmd("VERSION")
        assert "+" in r  # success indicator
        raiden.cmd("API OFF")

    def test_api_error_protocol(self, raiden):
        raiden.cmd("API ON")
        r = raiden.cmd("FOOBAR")
        assert "!" in r  # error indicator
        raiden.cmd("API OFF")


# ── Trace state management ───────────────────────────────────

class TestTrace:

    def test_trace_status_idle(self, raiden):
        raiden.cmd("TRACE RESET")
        r = raiden.cmd("TRACE STATUS")
        assert "IDLE" in r

    def test_trace_no_args_is_status(self, raiden):
        raiden.cmd("TRACE RESET")
        r = raiden.cmd("TRACE")
        assert "IDLE" in r or "Trace" in r

    def test_trace_rate_set(self, raiden):
        r = raiden.cmd("TRACE RATE 50")
        assert "OK" in r or "clkdiv" in r

    def test_trace_reset(self, raiden):
        r = raiden.cmd("TRACE RESET")
        assert "ERROR" not in r


# ── ADC one-shot reads (official 0-based channel naming) ─────

class TestAdc:

    def test_adc_channel0_is_gp26(self, raiden):
        r = raiden.cmd("ADC 0")
        assert "ADC0" in r and "GP26" in r and "voltage" in r

    def test_adc_channel1_is_gp27(self, raiden):
        r = raiden.cmd("ADC 1")
        assert "ADC1" in r and "GP27" in r and "voltage" in r

    def test_adc_bare_reads_both(self, raiden):
        r = raiden.cmd("ADC")
        assert "ADC0" in r and "ADC1" in r

    def test_adc_old_one_based_selector_rejected(self, raiden):
        # Channels are 0-based now; the old 1-based 'ADC 2' must error.
        r = raiden.cmd("ADC 2")
        assert "ERROR" in r


# ── Breakpoint management ────────────────────────────────────

class TestBreakpoints:

    def test_bp_list_empty(self, raiden):
        raiden.cmd("SET BP CLEAR ALL")
        r = raiden.cmd("SET BP LIST")
        assert "Breakpoints" in r or "none" in r.lower()

    def test_bp_clear_all(self, raiden):
        r = raiden.cmd("SET BP CLEAR ALL")
        assert "ERROR" not in r


# ── SWD speed ────────────────────────────────────────────────

class TestSWDSpeed:

    def test_swd_speed_query(self, raiden):
        r = raiden.cmd("SWD SPEED")
        assert "delay" in r.lower() or "kHz" in r or "speed" in r.lower()

    def test_swd_speed_set_and_read(self, raiden):
        raiden.cmd("SWD SPEED 2")
        r = raiden.cmd("SWD SPEED")
        assert "2" in r

        # Restore default
        raiden.cmd("SWD SPEED 1")


# ── SWD DISCONNECT ───────────────────────────────────────────
# Regression guard: DISCONNECT is handled by the executor (swd_deinit -> pins
# high-Z) and listed in HELP, but was missing from the swd_subcmds[] whitelist,
# so the abbreviation gate rejected it with "Unknown SWD sub-command" before it
# ever ran. Pure config: it only releases the SWD pins (swd_deinit early-returns
# when not connected), so no target is needed.

class TestSWDDisconnect:

    def test_disconnect_is_accepted(self, raiden):
        r = raiden.cmd("SWD DISCONNECT")
        assert "ERROR" not in r
        assert "disconnected" in r.lower()

    def test_unknown_swd_subcommand_still_errors(self, raiden):
        r = raiden.cmd("SWD WIBBLE")
        assert "ERROR" in r and "Unknown" in r


# ── SWD PHY (bit-bang vs. PIO physical layer) ─────────
# SWD PHY PIO only records mode + frequency (the PIO program itself is
# claimed lazily inside swd_connect_ex()) -- safe with no target wired,
# same reasoning as SWD SPEED being pure config. Every test restores
# BITBANG at the end so it doesn't leak into later tests/modules.

class TestSWDPhy:

    def test_phy_query_reports_selected_mode(self, raiden):
        # Sets the mode explicitly rather than asserting the boot default:
        # the default is only observable on a freshly-booted device, and any
        # earlier test (or manual session) that selected PIO would otherwise
        # make this fail for a reason that has nothing to do with the code.
        raiden.cmd("SWD PHY BITBANG")
        assert "BITBANG" in raiden.cmd("SWD PHY")
        raiden.cmd("SWD PHY PIO 2500")
        assert "PIO" in raiden.cmd("SWD PHY")
        raiden.cmd("SWD PHY BITBANG")

    def test_phy_unknown_mode(self, raiden):
        assert "ERROR" in raiden.cmd("SWD PHY WIBBLE")

    # No test here for "SWD PHY PIO with no khz and none ever configured" --
    # phy_khz_last is a static that only resets on reboot, so that specific
    # refusal (swd_set_phy_mode() returning false) is only deterministically
    # true right after flashing/rebooting, not reliably reproducible against
    # a live device that another test in this run may have already put into
    # PIO mode. test_phy_pio_zero_khz below covers the CLI-level "0 is
    # never valid" rejection, which is the reliably-testable half of this.

    def test_phy_pio_bad_khz(self, raiden):
        assert "ERROR" in raiden.cmd("SWD PHY PIO notanumber")

    def test_phy_pio_zero_khz(self, raiden):
        assert "ERROR" in raiden.cmd("SWD PHY PIO 0")

    def test_phy_bitbang_takes_no_arg(self, raiden):
        assert "ERROR" in raiden.cmd("SWD PHY BITBANG 5")

    def test_phy_pio_set_and_query(self, raiden):
        r = raiden.cmd("SWD PHY PIO 2500")
        assert "OK" in r and "2500" in r
        r = raiden.cmd("SWD PHY")
        assert "PIO" in r and "2500" in r
        raiden.cmd("SWD PHY BITBANG")

    def test_phy_pio_reuses_last_khz(self, raiden):
        raiden.cmd("SWD PHY PIO 2000")
        raiden.cmd("SWD PHY BITBANG")
        r = raiden.cmd("SWD PHY PIO")   # no khz given -- reuses 2000
        assert "OK" in r and "2000" in r
        raiden.cmd("SWD PHY BITBANG")


# ── SWD BENCH. Exempted from auto-connect (see command_parser.c) so it can
# run here whether or not a target happens to be wired -- config_none means
# "needs no PARTICULAR wiring", not "guaranteed nothing attached". The
# invariant that holds either way: it answers promptly, reports a
# microsecond figure, and never hangs. Asserting failure specifically was
# wrong -- it broke the moment a working BAT32 was on the bench.

class TestSWDBench:

    def test_bench_answers_with_a_timing(self, raiden):
        raiden.cmd("SWD PHY BITBANG")
        r = raiden.cmd("SWD BENCH", wait=1.5)
        assert "SWD BENCH" in r and "us" in r
        assert ("OK:" in r) or ("ERROR" in r)

    def test_bench_reports_phase_split_on_success(self, raiden):
        """If a target IS attached, the per-phase split must be present --
        that breakdown is the whole point of the command (a bare total sent
        an optimisation attempt after the wrong thing twice, see v0.10)."""
        raiden.cmd("SWD PHY BITBANG")
        r = raiden.cmd("SWD BENCH", wait=1.5)
        if "OK:" not in r:
            pytest.skip("No SWD target attached; nothing to split")
        for field in ("connect=", "ahb=", "read="):
            assert field in r, f"SWD BENCH success line missing {field}"


# ── ChipSHOUTER command guardrails (error paths) ─────────────
# These reject bad input in the firmware BEFORE anything is sent to the
# ChipSHOUTER, so they're safe with no CS connected (no arm/fire). Happy paths
# (CS ARM/FIRE/VOLTAGE) drive HV and need the CS, so they're not tested here.

class TestCsCommands:

    def test_cs_unknown_subcommand(self, raiden):
        assert "ERROR" in raiden.cmd("CS WIBBLE")

    def test_cs_voltage_out_of_range(self, raiden):
        r = raiden.cmd("CS VOLTAGE 999")          # > 500V max
        if _console_owns_uart0(raiden):
            assert "ERROR" in r and "UART0" in r
        else:
            assert "ERROR" in r and "range" in r.lower()

    def test_cs_voltage_too_low(self, raiden):
        assert "ERROR" in raiden.cmd("CS VOLTAGE 100")   # < 150V min

    def test_cs_voltage_garbage(self, raiden):
        assert "ERROR" in raiden.cmd("CS VOLTAGE abc")

    def test_cs_pulse_out_of_range(self, raiden):
        r = raiden.cmd("CS PULSE 50")             # < 80ns min
        if _console_owns_uart0(raiden):
            assert "ERROR" in r and "UART0" in r
        else:
            assert "ERROR" in r and "range" in r.lower()

    def test_cs_pulse_garbage(self, raiden):
        assert "ERROR" in raiden.cmd("CS PULSE xyz")

    def test_cs_trigger_bad_polarity(self, raiden):
        assert "ERROR" in raiden.cmd("CS TRIGGER HW BOGUS")


# ── SWD RACE (reset-release race) — error paths only. ─────────
# The happy path (an actual connect attempt) drives nRST + target power
# via target_power_ensure_on(), so it lives in test_config_swd.py behind
# --config=swd, same gating as SWD CONNECT's own auto-power test. Every
# case below is validated BEFORE any hardware is touched (see the ordering
# comment on the SWD RACE handler in command_parser.c) — safe here.

class TestSWDRaceErrors:

    def test_race_requires_phy_pio(self, raiden):
        raiden.cmd("SWD PHY BITBANG")
        r = raiden.cmd("SWD RACE")
        assert "ERROR" in r and "PHY" in r
        raiden.cmd("SWD PHY BITBANG")  # restore default (no-op, already BITBANG)

    def test_race_bad_delay(self, raiden):
        raiden.cmd("SWD PHY PIO 2500")
        r = raiden.cmd("SWD RACE notanumber")
        assert "ERROR" in r
        raiden.cmd("SWD PHY BITBANG")

    def test_race_sweep_missing_args(self, raiden):
        raiden.cmd("SWD PHY PIO 2500")
        r = raiden.cmd("SWD RACE SWEEP 100")
        assert "ERROR" in r
        raiden.cmd("SWD PHY BITBANG")

    def test_race_sweep_bad_range(self, raiden):
        raiden.cmd("SWD PHY PIO 2500")
        r = raiden.cmd("SWD RACE SWEEP 100 0 10")   # end < start
        assert "ERROR" in r
        raiden.cmd("SWD PHY BITBANG")

    def test_race_sweep_zero_step(self, raiden):
        raiden.cmd("SWD PHY PIO 2500")
        r = raiden.cmd("SWD RACE SWEEP 0 100 0")
        assert "ERROR" in r
        raiden.cmd("SWD PHY BITBANG")

    def test_race_sweep_bad_shots_keyword(self, raiden):
        raiden.cmd("SWD PHY PIO 2500")
        r = raiden.cmd("SWD RACE SWEEP 0 100 10 WIBBLE 3")
        assert "ERROR" in r
        raiden.cmd("SWD PHY BITBANG")

    def test_race_sweep_missing_shots_value(self, raiden):
        raiden.cmd("SWD PHY PIO 2500")
        r = raiden.cmd("SWD RACE SWEEP 0 100 10 SHOTS")
        assert "ERROR" in r
        raiden.cmd("SWD PHY BITBANG")

    def test_race_sweep_too_large(self, raiden):
        raiden.cmd("SWD PHY PIO 2500")
        r = raiden.cmd("SWD RACE SWEEP 0 100000000 1")   # way over MAX_POINTS
        assert "ERROR" in r
        raiden.cmd("SWD PHY BITBANG")


# ── SET/GET value validation (parse_u32, not atoi) ────────────

class TestSetValueValidation:

    def test_set_pause_garbage_errors(self, raiden):
        r = raiden.cmd("SET PAUSE notanumber")
        assert "ERROR" in r

    def test_set_width_garbage_does_not_apply(self, raiden):
        raiden.cmd("SET WIDTH 150")
        r = raiden.cmd("SET WIDTH xyz")
        assert "ERROR" in r
        # A rejected SET must not silently overwrite the old value with 0
        # (the old atoi() behavior) — WIDTH should still read 150.
        r2 = raiden.cmd("GET WIDTH")
        assert "150" in r2

    def test_set_pause_hex_accepted(self, raiden):
        raiden.cmd("SET PAUSE 0x100")
        r = raiden.cmd("GET PAUSE")
        assert "256" in r
        raiden.cmd("SET PAUSE 1000")  # restore a benign default


# ── Prefix matching ──────────────────────────────────────────

class TestPrefixMatching:

    def test_stat_matches_status(self, raiden):
        r = raiden.cmd("STAT")
        assert "System Status" in r or "RP2350" in r

    def test_ver_matches_version(self, raiden):
        r = raiden.cmd("VER")
        assert "Raiden Pico" in r

    def test_pin_matches_pins(self, raiden):
        r = raiden.cmd("PIN")
        assert "Pin Configuration" in r or "GP" in r
