/*
 * CLI reseau et discipline temps reel -- variante Pico 2 W uniquement.
 *
 * Etat : Wi-Fi + poll + sections silencieuses. Le serveur TCP et le pilote
 * stdio ne sont PAS encore la (etape 5 du plan) : ce fichier permet
 * aujourd'hui d'associer la carte, de mesurer l'effet du Wi-Fi sur les
 * fenetres de glitch, et de valider les trois pieges de ressources.
 *
 * Deux regles gouvernent tout ce fichier :
 *
 *   1. lwIP et le pilote CYW43 ne travaillent QUE dans net_cli_poll().
 *      C'est pour cela qu'on lie pico_cyw43_arch_lwip_poll et non
 *      _threadsafe_background : cette derniere execute la pile depuis une
 *      interruption de fond, exactement l'asynchronisme incontrolable qu'un
 *      firmware de glitch ne peut pas tolerer.
 *
 *   2. Pendant une fenetre critique, le reseau se tait completement.
 *      Meme en mode poll, cyw43_irq_init() arme une interruption GPIO de
 *      NIVEAU sur le host-wake, sur IO_IRQ_BANK0 -- le vecteur dont
 *      target_uart.c:1513 dit, mesures internes a l'appui, que quelques
 *      instructions de dispatch suffisent a faire rater la restauration du
 *      rail dans la boucle ADC. net_quiet_enter() masque cette broche.
 */

#include "net_cli.h"

#if RAIDEN_HAS_WIFI

#include "pico/cyw43_arch.h"
#include "pico/time.h"
#include "hardware/gpio.h"
#include <stdio.h>

#ifndef WIFI_SSID
#define WIFI_SSID ""
#define WIFI_PASSWORD ""
#endif

// Profondeur d'imbrication des sections silencieuses. Volatile : lue par le
// filet de securite, ecrite depuis les sections critiques.
static volatile uint32_t net_quiet_depth = 0;
static uint64_t quiet_since_us = 0;
static bool wifi_up = false;

// Au-dela de cette duree, une section silencieuse est forcement une fuite :
// la plus longue legitime (la boucle ADC d'un tir) se compte en millisecondes.
#define NET_QUIET_LEAK_US 2000000ull

void net_quiet_enter(void) {
    if (net_quiet_depth++ == 0) {
        // CETTE broche seulement. Jamais irq_set_enabled(IO_IRQ_BANK0,
        // false) : cela couperait aussi nrst_irq_handler et
        // trace_glitch_fired_isr, que les sections critiques existent
        // precisement pour servir.
        gpio_set_irq_enabled(CYW43_PIN_WL_HOST_WAKE, GPIO_IRQ_LEVEL_HIGH, false);
        quiet_since_us = time_us_64();
    }
}

void net_quiet_exit(void) {
    if (net_quiet_depth == 0)
        return;                       // desequilibre : ne pas boucler sous zero
    if (--net_quiet_depth == 0) {
        // L'interruption est sur NIVEAU : si le module a demande de
        // l'attention pendant la section, elle se redeclenche des le
        // reamorcage. Rien n'est perdu, tout au plus retarde.
        gpio_set_irq_enabled(CYW43_PIN_WL_HOST_WAKE, GPIO_IRQ_LEVEL_HIGH, true);
    }
}

bool net_cli_init(void) {
    if (cyw43_arch_init()) {
        printf("[NET] cyw43_arch_init a echoue\r\n");
        return false;
    }

    if (WIFI_SSID[0] == '\0') {
        printf("[NET] Wi-Fi present, aucun SSID compile "
               "(-DWIFI_SSID=... au cmake)\r\n");
        return true;
    }

    cyw43_arch_enable_sta_mode();
    printf("[NET] association a \"%s\"...\r\n", WIFI_SSID);
    if (cyw43_arch_wifi_connect_timeout_ms(WIFI_SSID, WIFI_PASSWORD,
                                           CYW43_AUTH_WPA2_AES_PSK, 30000)) {
        printf("[NET] association echouee\r\n");
        return false;
    }
    wifi_up = true;
    printf("[NET] IP = %s\r\n", ip4addr_ntoa(netif_ip4_addr(netif_default)));
    return true;
}

void net_cli_poll(void) {
    if (net_quiet_depth > 0) {
        // Filet : une section silencieuse qui dure est une sortie oubliee.
        // Le symptome serait autrement un Wi-Fi muet sans aucun message.
        if (time_us_64() - quiet_since_us > NET_QUIET_LEAK_US) {
            printf("[NET] section silencieuse fuitee (depth=%lu), forcage\r\n",
                   (unsigned long)net_quiet_depth);
            net_quiet_depth = 1;
            net_quiet_exit();
        }
        return;
    }
    cyw43_arch_poll();
}

void net_cli_pump(void) {
    net_cli_poll();
}

void net_sleep_ms(uint32_t ms) {
    // Decoupe l'attente pour que le lien ne meure pas pendant les 26 sleep_ms
    // du parseur. En section silencieuse, net_cli_poll() rend la main
    // immediatement : la fonction redevient alors un sleep_ms ordinaire, ce
    // qui est le comportement voulu.
    absolute_time_t end = make_timeout_time_ms(ms);
    while (!time_reached(end)) {
        net_cli_poll();
        sleep_ms(1);
    }
}

#endif // RAIDEN_HAS_WIFI
