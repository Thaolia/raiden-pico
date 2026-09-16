"""Config SWD: Tests requiring an STM32F1 target connected via SWD.

Wiring:
    GP17  -> STM32 SWCLK
    GP18  -> STM32 SWDIO
    GP15  -> STM32 nRST
    GP13  -> STM32 BOOT0
    GP10/11/12 -> Target VDD (or external power)
    Target must be powered and responsive.
"""

import pytest


pytestmark = pytest.mark.config_swd


def _require_rdp0(swd_target):
    """Skip test if target is not at RDP Level 0 (flash inaccessible)."""
    r = swd_target.cmd("SWD RDP")
    if "Level 0" not in r:
        pytest.skip("Target not at RDP0 — flash access blocked")


# ── Connection ───────────────────────────────────────────────

class TestSWDConnect:

    def test_connect(self, swd_target):
        r = swd_target.cmd("SWD CONNECT")
        assert "Connected" in r
        assert "DPIDR" in r

    def test_connect_idempotent(self, swd_target):
        swd_target.cmd("SWD CONNECT")
        r = swd_target.cmd("SWD CONNECT")
        assert "Connected" in r

    def test_connectrst(self, swd_target):
        r = swd_target.cmd("SWD CONNECTRST", wait=2)
        assert "Connected" in r

    def test_disconnect_reconnect(self, swd_target):
        swd_target.cmd("SWD DISCONNECT")
        r = swd_target.cmd("SWD CONNECT")
        assert "Connected" in r

    def test_idcode(self, swd_target):
        r = swd_target.cmd("SWD IDCODE")
        assert "DPIDR" in r
        assert "CPUID" in r
        assert "Cortex" in r

    def test_help_text(self, swd_target):
        r = swd_target.cmd("SWD")
        for sub in ["CONNECT", "CONNECTRST", "DISCONNECT", "READ", "WRITE",
                     "FILL", "HALT", "RESUME", "RESET", "RDP", "OPT",
                     "REGS", "SETREG", "SPEED", "BPTEST", "RACE"]:
            assert sub in r, f"SWD help missing: {sub}"

    def test_prefix_ambiguous(self, swd_target):
        r = swd_target.cmd("SWD CON")
        assert "Ambiguous" in r

    def test_prefix_unambiguous(self, swd_target):
        r = swd_target.cmd("SWD ID")
        assert "DPIDR" in r

    def test_unknown_subcmd(self, swd_target):
        r = swd_target.cmd("SWD FOOBAR")
        assert "ERROR" in r


# ── Memory Read ──────────────────────────────────────────────

class TestSWDRead:

    def test_read_cpuid(self, swd_target):
        swd_target.cmd("SWD HALT")
        r = swd_target.cmd("SWD READ E000ED00")
        assert "E000ED00:" in r

    def test_read_count(self, swd_target):
        r = swd_target.cmd("SWD READ 20000000 4")
        assert "Reading 16 bytes" in r

    def test_read_sram_alias(self, swd_target):
        r = swd_target.cmd("SWD READ SRAM 4")
        assert "Reading 16 bytes" in r
        assert "20000000:" in r

    def test_read_flash_alias(self, swd_target):
        _require_rdp0(swd_target)
        r = swd_target.cmd("SWD READ FLASH 4")
        assert "Reading 16 bytes" in r
        assert "08000000:" in r

    def test_read_dp(self, swd_target):
        r = swd_target.cmd("SWD READ DP 0")
        assert "DP[0x0]" in r

    def test_read_ap(self, swd_target):
        r = swd_target.cmd("SWD READ AP FC")
        assert "AP[0xFC]" in r

    def test_read_missing_addr(self, swd_target):
        r = swd_target.cmd("SWD READ DP")
        assert "ERROR" in r


# ── Memory Write ─────────────────────────────────────────────

class TestSWDWrite:

    def test_write_sram_verify(self, swd_target):
        swd_target.cmd("SWD HALT")
        r = swd_target.cmd("SWD WRITE 20000000 DEADBEEF")
        assert "verified" in r

    def test_write_sram_readback(self, swd_target):
        swd_target.cmd("SWD WRITE 20000000 12345678")
        r = swd_target.cmd("SWD READ 20000000")
        assert "78 56 34 12" in r

    def test_write_flash_prompts_erase(self, swd_target):
        r = swd_target.cmd("SWD WRITE 08000000 DEADBEEF")
        assert "ERASE" in r

    @pytest.mark.destructive
    def test_write_flash_with_erase(self, swd_target):
        _require_rdp0(swd_target)
        r = swd_target.cmd("SWD WRITE 08000000 DEADBEEF ERASE", wait=3)
        assert "verified" in r

    def test_write_dp(self, swd_target):
        r = swd_target.cmd("SWD WRITE DP 0 1E")
        assert "DP[0x0]" in r


# ── Memory Fill ──────────────────────────────────────────────

class TestSWDFill:

    def test_fill_sram(self, swd_target):
        swd_target.cmd("SWD HALT")
        r = swd_target.cmd("SWD FILL 20000000 A5A5A5A5 8")
        assert "Filled 8 words" in r
        assert "verified" in r

    def test_fill_sram_readback(self, swd_target):
        swd_target.cmd("SWD FILL 20000000 A5A5A5A5 8")
        r = swd_target.cmd("SWD READ 20000000 8")
        assert "A5 A5 A5 A5" in r

    def test_fill_missing_args(self, swd_target):
        r = swd_target.cmd("SWD FILL")
        assert "ERROR" in r

    def test_fill_flash_prompts_erase(self, swd_target):
        r = swd_target.cmd("SWD FILL FLASH DEADBEEF 4")
        assert "ERASE" in r


# ── Core Registers ───────────────────────────────────────────

class TestSWDRegs:

    def test_regs_all(self, swd_target):
        swd_target.cmd("SWD HALT")
        r = swd_target.cmd("SWD REGS", wait=2)
        for reg in ["r0", "sp", "lr", "pc", "xPSR"]:
            assert reg in r, f"REGS missing: {reg}"

    def test_setreg_readback(self, swd_target):
        swd_target.cmd("SWD HALT")
        swd_target.cmd("SWD SETREG R0 AABBCCDD")
        r = swd_target.cmd("SWD REGS", wait=2)
        assert "AABBCCDD" in r.upper()


# ── Target Reset ─────────────────────────────────────────────

class TestSWDReset:

    def test_reset_pulse(self, swd_target):
        r = swd_target.cmd("SWD RESET", wait=1.5)
        assert "OK" in r

    def test_reconnect_after_reset(self, swd_target):
        swd_target.cmd("SWD RESET", wait=1.5)
        r = swd_target.cmd("SWD CONNECT")
        assert "Connected" in r

    def test_reset_hold_release(self, swd_target):
        r = swd_target.cmd("SWD RESET HOLD")
        assert "OK" in r

        r = swd_target.cmd("SWD RESET RELEASE")
        assert "OK" in r

        r = swd_target.cmd("SWD CONNECT")
        assert "Connected" in r

    def test_halt_resume(self, swd_target):
        swd_target.cmd("SWD CONNECT")
        r = swd_target.cmd("SWD HALT")
        assert "OK" in r

        r = swd_target.cmd("SWD RESUME")
        assert "OK" in r


# ── Flash Operations ─────────────────────────────────────────

@pytest.mark.destructive
class TestSWDFlash:

    def test_flash_erase_page0(self, swd_target):
        _require_rdp0(swd_target)
        swd_target.cmd("SWD HALT")
        r = swd_target.cmd("SWD FLASH ERASE 0", wait=2)
        assert "OK" in r

    def test_flash_erased_is_ff(self, swd_target):
        _require_rdp0(swd_target)
        swd_target.cmd("SWD HALT")
        swd_target.cmd("SWD FLASH ERASE 0", wait=2)
        r = swd_target.cmd("SWD READ 08000000 4")
        assert "FF FF FF FF" in r

    def test_flash_write_persists_reset(self, swd_target):
        _require_rdp0(swd_target)
        swd_target.cmd("SWD HALT")
        swd_target.cmd("SWD WRITE 08000000 F00DCAFE ERASE", wait=3)
        swd_target.cmd("SWD RESET", wait=1.5)
        swd_target.cmd("SWD CONNECT")
        swd_target.cmd("SWD HALT")
        r = swd_target.cmd("SWD READ 08000000")
        assert "FE CA 0D F0" in r


# ── Option Bytes ─────────────────────────────────────────────

class TestSWDOpt:

    def test_opt_read(self, swd_target):
        swd_target.cmd("SWD HALT")
        r = swd_target.cmd("SWD OPT")
        assert "RDP" in r
        assert "ERROR" not in r

    def test_rdp_read(self, swd_target):
        r = swd_target.cmd("SWD RDP")
        assert "RDP Level" in r


# ── Error Recovery ───────────────────────────────────────────

class TestSWDErrors:

    def test_bad_addr_recovers(self, swd_target):
        swd_target.cmd("SWD HALT")
        swd_target.cmd("SWD READ FFFFFFFC")  # triggers STICKYERR
        r = swd_target.cmd("SWD READ E000ED00")
        assert "E000ED00:" in r

    def test_multiple_errors_recover(self, swd_target):
        swd_target.cmd("SWD READ FFFFFFFC")
        swd_target.cmd("SWD READ FFFFFFFC")
        swd_target.cmd("SWD READ FFFFFFFC")
        r = swd_target.cmd("SWD READ SRAM 4")
        assert "Read complete" in r

    def test_sram_works_after_error(self, swd_target):
        swd_target.cmd("SWD HALT")
        swd_target.cmd("SWD READ FFFFFFFC")
        r = swd_target.cmd("SWD WRITE 20000000 AABBCCDD")
        assert "verified" in r

    def test_halt_when_halted(self, swd_target):
        swd_target.cmd("SWD HALT")
        r = swd_target.cmd("SWD HALT")
        assert "OK" in r


# ── Trigger Fire (requires UART GP4/GP5 wiring) ────────────

class TestTriggerFire:
    """Verify triggers actually fire. Requires UART wiring (GP4/GP5)."""

    def _cleanup(self, cli):
        cli.cmd("ARM OFF")
        cli.cmd("TRIGGER NONE")

    def _arm_ok(self, cli):
        """Arm and return True only if ARM ON actually succeeded."""
        r = cli.cmd("ARM ON")
        return "OK" in r and "ERROR" not in r

    def test_uart_tx_trigger_fires(self, swd_target):
        """ARM with UART TX trigger, send the trigger byte, confirm it fires."""
        cli = swd_target
        self._cleanup(cli)

        # Init target UART first so PIO trigger sets up after pin is configured
        cli.cmd("TARGET SEND 00", wait=0.5)

        cli.cmd("TRIGGER UART 0D TX")
        assert self._arm_ok(cli), "Failed to arm — PIO may be full"

        r = cli.cmd("ARM")
        assert "DISARMED" not in r, "System disarmed before trigger sent"

        # Send the trigger byte on TX — PIO UART decoder should match
        cli.cmd("TARGET SEND 0D", wait=0.5)

        # Trigger should have fired → system disarmed
        r = cli.cmd("ARM")
        assert "DISARMED" in r, "UART TX trigger did not fire"

        self._cleanup(cli)

    def test_uart_tx_wrong_byte_no_fire(self, swd_target):
        """ARM with UART TX trigger for 0D, send different byte, should stay armed."""
        cli = swd_target
        self._cleanup(cli)

        # Init target UART first
        cli.cmd("TARGET SEND 00", wait=0.5)

        cli.cmd("TRIGGER UART 0D TX")
        assert self._arm_ok(cli), "Failed to arm — PIO may be full"

        # Send a non-matching byte
        cli.cmd("TARGET SEND AA", wait=0.5)

        # Should still be armed (wrong byte)
        r = cli.cmd("ARM")
        assert "DISARMED" not in r, "False trigger — system disarmed on wrong byte (0xAA)"

        self._cleanup(cli)

    def test_uart_rx_trigger_fires(self, swd_target):
        """ARM with UART RX trigger, enter bootloader, sync triggers ACK (0x79)."""
        cli = swd_target
        self._cleanup(cli)

        # Put target in bootloader mode via BOOT0 + reset
        cli.cmd("TARGET STM32F1")
        r = cli.cmd("TARGET SYNC", wait=15)
        if "ACK" not in r:
            self._cleanup(cli)
            pytest.skip("TARGET SYNC failed — bootloader not responding on UART")

        # Now set RX trigger for ACK byte and arm
        cli.cmd("TRIGGER UART 79 RX")
        assert self._arm_ok(cli), "Failed to arm — PIO may be full"

        r = cli.cmd("ARM")
        assert "DISARMED" not in r, "System disarmed before sending command"

        # Send GET command (0x00 0xFF) — bootloader replies with ACK (0x79) + data
        cli.cmd("TARGET SEND 00FF", wait=1)

        r = cli.cmd("ARM")
        assert "DISARMED" in r, "UART RX trigger did not fire on bootloader ACK"

        self._cleanup(cli)

    def test_gpio_trigger_fires(self, swd_target):
        """ARM with GPIO RISING trigger on GP3, confirm PIO loads and arms.

        Note: Actually firing the trigger requires GP3 wired to a signal source.
        The default trigger pin (GP3) is NOT wired to nRST (GP15), so we only
        test that arming succeeds (PIO program loads) and the system stays armed.
        """
        cli = swd_target
        self._cleanup(cli)

        cli.cmd("TRIGGER GPIO RISING")
        if not self._arm_ok(cli):
            self._cleanup(cli)
            pytest.skip("PIO0 full — cannot load GPIO trigger")

        r = cli.cmd("ARM")
        assert "ARMED" in r, "GPIO trigger did not arm properly"

        self._cleanup(cli)


# ── SWD RACE (reset-release race) — happy path, needs a live target ──
# The pure-parsing error paths (bad delay/sweep args, PHY gate) don't
# touch hardware and are covered without wiring in
# test_config_none.py::TestSWDRaceErrors. This class exercises the actual
# nRST-assert/release + connect sequence against a live SW-DP, over the
# PIO physical layer (SWD PHY PIO) -- SWD RACE no longer accepts SPEED 0,
# see the v0.10 CHANGELOG entry and §0bis of 07_BAT32G135_FAULTYCAT.md for
# why (SPEED 0 mis-samples on a flying-wire bench; it isn't just slow).

class TestSWDRace:

    def test_race_single_shot_runs(self, swd_target):
        """No BAT32 is wired, so this can never reach SUCCESS (TARGET
        stays STM32F1 -> bat32_get_target_info() returns NULL -> the
        plausibility check is disabled) — it only confirms the
        reset-release + connect sequence runs end-to-end against a real
        SW-DP without wedging the CLI or the SWD state machine, and that
        the target is still reachable normally afterward.
        """
        cli = swd_target
        cli.cmd("TARGET STM32F1")
        cli.cmd("SWD PHY PIO 2500")
        r = cli.cmd("SWD RACE 0", wait=2)
        assert "RACE delay=" in r
        assert any(cat in r for cat in
                   ("no_dp", "dp_only", "mem_blocked", "perturbed", "SUCCESS"))

        cli.cmd("SWD PHY BITBANG")  # restore default
        r2 = cli.cmd("SWD CONNECT")
        assert "Connected" in r2

    def test_race_tiny_sweep_runs(self, swd_target):
        """A small, bounded sweep (3 delay points x 1 shot) — checks the
        sweep loop itself runs to completion and reports a summary, not
        that it finds anything (SUCCESS is unreachable, see above)."""
        cli = swd_target
        cli.cmd("TARGET STM32F1")
        cli.cmd("SWD PHY PIO 2500")
        r = cli.cmd("SWD RACE SWEEP 0 20 10", wait=5)
        assert ("sweep complete" in r.lower() or
                "ERROR: SWD RACE SWEEP finished without a SUCCESS" in r)

        cli.cmd("SWD PHY BITBANG")
        r2 = cli.cmd("SWD CONNECT")
        assert "Connected" in r2


# ── TARGET BAT32 refusal paths (no write/erase path exists) ──────────
# Uses the live STM32 SW-DP so auto-connect succeeds, then overrides the
# software-selected TARGET to BAT32 to reach the BAT32-specific gates —
# these check the SOFTWARE target type, not what chip is actually wired.

class TestTargetBat32Refusals:

    def test_bat32_rdp_refused(self, swd_target):
        cli = swd_target
        cli.cmd("TARGET BAT32")
        r = cli.cmd("SWD RDP")
        assert "ERROR" in r and "RDP register" in r
        cli.cmd("TARGET STM32F1")  # restore for later tests in the module

    def test_bat32_flash_erase_refused(self, swd_target):
        cli = swd_target
        cli.cmd("TARGET BAT32")
        r = cli.cmd("SWD FLASH ERASE 0")
        assert "ERROR" in r and "read-only" in r
        cli.cmd("TARGET STM32F1")

    def test_bat32_opt_runs(self, swd_target):
        """SWD OPT under TARGET BAT32 against a real (STM32) SW-DP.

        The addresses it reads (0xC0/0x1C0 option-byte clusters,
        0x500004 data flash, 0x4001B004 DBGSTOPCR) are not BAT32 option
        bytes on this chip — 0xC0/0x1C0 alias valid low STM32 flash so
        they should read fine, but 0x500004 is outside any STM32F1
        region and may bus-fault, which can leave the AP in a state
        where the DBGSTOPCR read after it fails too. So this only
        asserts the command doesn't return ERROR and the first (always
        reachable) OCDEN field prints — not that every field printed.
        """
        cli = swd_target
        cli.cmd("TARGET BAT32")
        r = cli.cmd("SWD OPT", wait=2)
        assert "ERROR" not in r
        assert "OCDEN" in r
        cli.cmd("TARGET STM32F1")
