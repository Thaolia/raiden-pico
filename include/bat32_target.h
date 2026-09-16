#ifndef BAT32_TARGET_H
#define BAT32_TARGET_H

#include <stdint.h>
#include <stdbool.h>
#include "config.h"

// Cmsemicon BAT32G135 (Cortex-M0+) — read-only target descriptor.
//
// Source: TPLink_Tapo/07_BAT32G135_FAULTYCAT.md §1-§2, citing the
// official Cmsemicon BAT32G135 User Manual V0.11 and Datasheet V1.40.
//
// Deliberately READ-ONLY: this descriptor and the code that consumes it
// (SWD OPT decoding, SWD RACE's plausibility check) never write to the
// flash controller. Making the Level 0 -> permanently-unlocked transition
// (writing OCDEN via FLPROT/FLOPMD1/FLOPMD2, [UM] §29.3-29.4) is
// destructive and out of scope for this descriptor — see the doc's §9bis.3
// / §12 item 8 if that is ever wanted.
typedef struct {
    const char *name;

    // Code flash: 0x00000000, 64KB, 128 x 512B sectors.
    uint32_t code_flash_base;
    uint32_t code_flash_size;

    // Data flash: 0x00500000, 1.5KB. Holds OCDM/BTEN (see below) — a
    // firmware that writes its own data flash carelessly can flip its
    // own protection level by accident ([UM] p.735).
    uint32_t data_flash_base;
    uint32_t data_flash_size;

    // SRAM: 0x20000000, 8KB, parity-protected.
    uint32_t sram_base;
    uint32_t sram_size;

    // 128-bit factory UID, read-only. Cmsemicon's own manual suggests
    // using it as a firmware-encryption key ([UM] §26.3.8) — check here
    // first if a dumped image looks encrypted.
    uint32_t uid_base;
    uint32_t uid_size;

    // Option bytes, cluster 0 (consulted when boot-swap BTEN=1, the
    // power-on default). OCDEN is the byte to fault/race for — see the
    // truth table in swd_bat32_decode_protection().
    uint32_t ocden_addr;         // 0x000000C3

    // Option bytes, cluster 1 (consulted instead of cluster 0 when
    // boot-swap is ACTIVE, BTEN=0 — [UM] §5.1/§28.1). If a target has
    // boot-swap on and the developer forgot to mirror the option bytes
    // here, THIS is the byte that actually gates protection.
    uint32_t ocden_addr_swap;    // 0x000001C3

    // Data-flash word holding OCDM (byte 0, 0x00500004) and BTEN (bit 0
    // of byte 1, 0x00500005) — same 32-bit word, read together.
    uint32_t ocdm_bten_addr;     // 0x00500004

    // DBGSTOPCR — bit 24 (SWDIS) disables the SWD pins at runtime when
    // the firmware writes it. This is the "third lock" the reset-release
    // race (SWD RACE) is racing against; it is 0 (SWD enabled) at reset.
    uint32_t dbgstopcr_addr;     // 0x4001B004
} bat32_target_info_t;

#define BAT32_OCDEN_PROTECTED   0xC3  // OCDEN value that enables protection
#define BAT32_OCDM_LEVEL2       0x3C  // OCDM value that selects Level 2 (with OCDEN==0xC3)
#define BAT32_DBGSTOPCR_SWDIS   (1u << 24)

// Protection level derived from OCDEN/OCDM, per [UM] §28.3 fig. 28-4.
typedef enum {
    BAT32_PROT_LEVEL0 = 0,  // OCDEN != 0xC3: read/write/erase all allowed
    BAT32_PROT_LEVEL1,      // OCDEN == 0xC3, OCDM != 0x3C: chip-erase only
    BAT32_PROT_LEVEL2,      // OCDEN == 0xC3, OCDM == 0x3C: no flash access at all
} bat32_prot_level_t;

static inline bat32_prot_level_t bat32_decode_level(uint8_t ocden, uint8_t ocdm) {
    if (ocden != BAT32_OCDEN_PROTECTED)
        return BAT32_PROT_LEVEL0;
    return (ocdm == BAT32_OCDM_LEVEL2) ? BAT32_PROT_LEVEL2 : BAT32_PROT_LEVEL1;
}

// Returns the BAT32G135 descriptor for TARGET_BAT32, NULL for anything
// else (mirrors stm32_get_target_info()'s NULL-on-unknown — never fabricate
// a default descriptor for an unselected/foreign target type).
const bat32_target_info_t *bat32_get_target_info(target_type_t type);

#endif // BAT32_TARGET_H
