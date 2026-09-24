"""TARGET GLITCH on the BAT32G135 — regression tests.

The BAT32 is a read-only target with no stm32_target_info_t, so TARGET GLITCH
used to refuse it (STM32 DBGMCU auto-detect -> "Unknown DEV_ID 0x000"). The
feat routes TARGET GLITCH TEST/SWEEP to the target-agnostic power_glitch_once()
primitive with a BAT32 flash/SRAM oracle.

Two gates, by SAFETY (see tests/conftest.py):
  * error paths (bad/unsupported sub-command) DON'T drive hardware -> config_none.
  * the actual dip (TEST/SWEEP with valid args) drives the GP10/11/12 power
    group -> config_power_int only, never config_none.
"""

import pytest


# ── Error paths — no hardware, safe under config_none ─────────

class TestBat32GlitchErrors:

    def test_unsupported_verb_errors(self, raiden):
        # A valid glitch verb that has no BAT32 meaning must error clearly,
        # naming the target — not fall through to the STM32 path.
        raiden.cmd("TARGET BAT32")
        r = raiden.cmd("TARGET GLITCH PAYLOAD")
        assert "ERROR" in r
        assert "unsupported for BAT32" in r

    def test_bypass_unsupported_for_bat32(self, raiden):
        raiden.cmd("TARGET BAT32")
        r = raiden.cmd("TARGET GLITCH BYPASS")
        assert "ERROR" in r
        assert "unsupported for BAT32" in r

    def test_test_without_voltage_errors(self, raiden):
        raiden.cmd("TARGET BAT32")
        r = raiden.cmd("TARGET GLITCH TEST")
        assert "ERROR" in r
        assert "Usage: TARGET GLITCH TEST" in r

    def test_test_bad_count_errors(self, raiden):
        raiden.cmd("TARGET BAT32")
        r = raiden.cmd("TARGET GLITCH TEST 2.0 notanumber")
        assert "ERROR" in r
        assert "Invalid count" in r

    def test_unknown_glitch_verb_errors(self, raiden):
        # Caught by match_and_replace before the BAT32 branch.
        raiden.cmd("TARGET BAT32")
        r = raiden.cmd("TARGET GLITCH WIBBLE")
        assert "ERROR" in r
        assert "Unknown GLITCH command" in r

    # SWD GLITCH / SWD GLITCH SWEEP — reset-synced voltage glitch. These error
    # paths are pure parser validation: they return BEFORE the INTERNAL/BAT32
    # gate and before any hardware, so they are power-mode-independent and safe
    # under config_none. (SWD GLITCH is excluded from the SWD auto-connect, so a
    # malformed one never opens a connection either.)

    def test_swd_glitch_no_args_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH")
        assert "ERROR" in r
        assert "Usage: SWD GLITCH" in r

    def test_swd_glitch_missing_volt_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH 100")
        assert "ERROR" in r
        assert "Usage: SWD GLITCH" in r

    def test_swd_glitch_unknown_option_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH 100 2.0 BOGUS")
        assert "ERROR" in r
        assert "Unknown SWD GLITCH option 'BOGUS'" in r

    def test_swd_glitch_sweep_too_few_args_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH SWEEP 0 200 5")
        assert "ERROR" in r
        assert "Usage: SWD GLITCH SWEEP" in r

    def test_swd_glitch_sweep_zero_delay_step_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH SWEEP 0 200 0 2400 3400 50")
        assert "ERROR" in r
        assert "delay range" in r

    def test_swd_glitch_sweep_bad_threshold_range_errors(self, raiden):
        # t1 < t0 and t1 > 4095 both invalid; here t1 < t0.
        r = raiden.cmd("SWD GLITCH SWEEP 0 200 5 3400 2400 50")
        assert "ERROR" in r
        assert "threshold range" in r

    def test_swd_glitch_sweep_threshold_over_full_scale_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH SWEEP 0 200 5 0 5000 50")
        assert "ERROR" in r
        assert "threshold range" in r

    def test_swd_glitch_sweep_unknown_option_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH SWEEP 0 200 5 2400 3400 50 BOGUS")
        assert "ERROR" in r
        assert "Unknown SWD GLITCH SWEEP option 'BOGUS'" in r

    def test_swd_glitch_sweep_shots_needs_count_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH SWEEP 0 200 5 2400 3400 50 SHOTS notanum")
        assert "ERROR" in r
        assert "SHOTS requires a count" in r

    def test_swd_glitch_sweep_too_large_errors(self, raiden):
        # 3001 delay pts x 4001 thr pts >> the 2000000-attempt cap.
        r = raiden.cmd("SWD GLITCH SWEEP 0 3000 1 0 4000 1")
        assert "ERROR" in r
        assert "too large" in r

    def test_swd_glitch_norst_is_a_valid_option(self, raiden):
        # NORST must be consumed as an option: the error names the trailing
        # unknown token, not NORST (proves NORST parses). Hardware-free: the
        # unknown-option error fires before any dip.
        r = raiden.cmd("SWD GLITCH 100 2.0 NORST BOGUS")
        assert "ERROR" in r
        assert "Unknown SWD GLITCH option 'BOGUS'" in r

    def test_swd_glitch_sweep_norst_is_a_valid_option(self, raiden):
        r = raiden.cmd("SWD GLITCH SWEEP 0 200 5 2400 3400 50 NORST BOGUS")
        assert "ERROR" in r
        assert "Unknown SWD GLITCH SWEEP option 'BOGUS'" in r

    def test_swd_glitch_settle_needs_value_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH 100 2.0 SETTLE")
        assert "ERROR" in r
        assert "SETTLE requires a value" in r

    def test_swd_glitch_settle_is_a_valid_option(self, raiden):
        # SETTLE consumes its value (0 here); the error names the trailing token.
        r = raiden.cmd("SWD GLITCH 100 2.0 SETTLE 0 BOGUS")
        assert "ERROR" in r
        assert "Unknown SWD GLITCH option 'BOGUS'" in r

    def test_swd_glitch_sweep_settle_is_a_valid_option(self, raiden):
        r = raiden.cmd("SWD GLITCH SWEEP 0 200 5 2400 3400 50 SETTLE 10 BOGUS")
        assert "ERROR" in r
        assert "Unknown SWD GLITCH SWEEP option 'BOGUS'" in r

    # SWD GLITCH PIO — reset-synced PIO pulse (EMFI/crowbar). Parser-level errors are
    # hardware-free (they return before arming the PIO or touching power/reset).

    def test_swd_glitch_pio_no_args_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH PIO")
        assert "ERROR" in r
        assert "Usage: SWD GLITCH PIO" in r

    def test_swd_glitch_pio_missing_width_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH PIO 100")
        assert "ERROR" in r
        assert "Usage: SWD GLITCH PIO" in r

    def test_swd_glitch_pio_unknown_option_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH PIO 100 20 BOGUS")
        assert "ERROR" in r
        assert "Unknown SWD GLITCH PIO option 'BOGUS'" in r

    def test_swd_glitch_pio_sweep_too_few_args_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH PIO SWEEP 0 100 5")
        assert "ERROR" in r
        assert "Usage: SWD GLITCH PIO SWEEP" in r

    def test_swd_glitch_pio_sweep_zero_step_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH PIO SWEEP 0 100 0 10 20 5")
        assert "ERROR" in r
        assert "PIO SWEEP range" in r

    def test_swd_glitch_pio_sweep_unknown_option_errors(self, raiden):
        r = raiden.cmd("SWD GLITCH PIO SWEEP 0 100 5 10 20 5 BOGUS")
        assert "ERROR" in r
        assert "Unknown SWD GLITCH PIO SWEEP option 'BOGUS'" in r

    def test_swd_glitch_pio_sweep_too_large_errors(self, raiden):
        # 200001 pause pts > the cap.
        r = raiden.cmd("SWD GLITCH PIO SWEEP 0 200000 1 10 20 5")
        assert "ERROR" in r
        assert "too large" in r


# ── Firing the dip — drives the power rail, config_power_int only ──

class TestBat32GlitchFire:

    @pytest.fixture(autouse=True)
    def _restore_internal(self, raiden):
        """Leave the device safe: disarmed, de-energized, INTERNAL."""
        yield
        raiden.cmd("ARM OFF")
        raiden.cmd("TARGET POWER OFF")
        raiden.cmd("TARGET POWER INT")

    @pytest.mark.config_power_int
    def test_test_fires_and_reports(self, raiden):
        raiden.cmd("TARGET BAT32")
        raiden.cmd("TARGET POWER INT")
        raiden.cmd("TARGET POWER ON")
        r = raiden.cmd("TARGET GLITCH TEST 2.0 1")
        # It ran the BAT32 path (not the STM32 auto-detect) and reported a tally.
        assert "BAT32 power glitch" in r
        assert "BAT32 glitch done" in r
        assert "Unknown DEV_ID" not in r

    @pytest.mark.config_power_int
    def test_sweep_fires_and_reports(self, raiden):
        raiden.cmd("TARGET BAT32")
        raiden.cmd("TARGET POWER INT")
        raiden.cmd("TARGET POWER ON")
        r = raiden.cmd("TARGET GLITCH SWEEP")
        assert "BAT32 glitch sweep" in r
        assert "Unknown DEV_ID" not in r

    # SWD GLITCH / SWD GLITCH SWEEP — reset-synced dip. NOPWR keeps the shot fast
    # (no per-shot power-cycle). With no real target wired the oracle connect just
    # fails (connfail/timeout) — we assert on the report format, which is emitted
    # regardless of outcome.

    @pytest.mark.config_power_int
    def test_swd_glitch_fires_and_reports(self, raiden):
        raiden.cmd("TARGET BAT32")
        raiden.cmd("TARGET POWER INT")
        raiden.cmd("TARGET POWER ON")
        r = raiden.cmd("SWD GLITCH 5 2.0 NOPWR")
        assert "reset-synced glitch" in r
        assert "GLITCH delay=5us" in r
        assert "TARGET BAT32 must be selected" not in r

    @pytest.mark.config_power_int
    def test_swd_glitch_sweep_fires_and_reports(self, raiden):
        raiden.cmd("TARGET BAT32")
        raiden.cmd("TARGET POWER INT")
        raiden.cmd("TARGET POWER ON")
        r = raiden.cmd("SWD GLITCH SWEEP 0 10 5 3000 3200 100 SHOTS 1 NOPWR")
        assert "SWD GLITCH SWEEP:" in r
        assert "TARGET BAT32 must be selected" not in r

    @pytest.mark.config_power_int
    def test_swd_glitch_voltage_out_of_range_errors(self, raiden):
        # Reaches the target fn but returns at the voltage check, before the dip.
        # Needs INTERNAL mode set (a pin-touching op), hence config_power_int.
        raiden.cmd("TARGET BAT32")
        raiden.cmd("TARGET POWER INT")
        r = raiden.cmd("SWD GLITCH 100 5.0")
        assert "ERROR" in r
        assert "voltage out of range" in r
