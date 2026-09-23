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
