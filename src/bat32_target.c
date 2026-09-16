/*
 * Cmsemicon BAT32G135 target descriptor (read-only)
 *
 * See include/bat32_target.h and TPLink_Tapo/07_BAT32G135_FAULTYCAT.md
 * §1-§2 for the sourcing of every field below.
 */

#include "bat32_target.h"
#include <stddef.h>

static const bat32_target_info_t bat32g135_info = {
    .name              = "BAT32G135",
    .code_flash_base   = 0x00000000,
    .code_flash_size   = 64 * 1024,
    .data_flash_base   = 0x00500000,
    .data_flash_size   = 1536,           // 1.5KB
    .sram_base         = 0x20000000,
    .sram_size         = 8 * 1024,
    .uid_base          = 0x0050084C,
    .uid_size          = 16,             // 128 bits
    .ocden_addr        = 0x000000C3,
    .ocden_addr_swap   = 0x000001C3,
    .ocdm_bten_addr    = 0x00500004,
    .dbgstopcr_addr    = 0x4001B004,
};

const bat32_target_info_t *bat32_get_target_info(target_type_t type) {
    switch (type) {
        case TARGET_BAT32: return &bat32g135_info;
        default: return NULL;
    }
}
