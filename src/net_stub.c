/*
 * Contrepartie non-Wi-Fi de net_cli.h : la seule fonction qui ne peut pas
 * etre un inline vide, parce qu'elle doit reellement attendre.
 */
#include "net_cli.h"
#include "pico/time.h"

#if !RAIDEN_HAS_WIFI
void net_sleep_ms(uint32_t ms) {
    sleep_ms(ms);
}
#endif
