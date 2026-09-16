/*
 * Reservation explicite des ressources PIO cablees en dur.
 *
 * Le firmware choisit ses PIO et ses state machines par constantes, sans
 * jamais appeler pio_sm_claim() : du point de vue du SDK, elles sont donc
 * TOUTES libres. Tant qu'aucune autre bibliotheque n'alloue de PIO, cela ne
 * se voit pas.
 *
 * Ce n'est plus vrai des qu'un pilote se sert dans le meme pot. Le pilote
 * CYW43 (Wi-Fi du Pico 2 W) appelle pio_claim_free_sm_and_add_program...(),
 * qui itere les PIO en ordre DECROISSANT (hardware_pio/pio.c:436-437) : il
 * demande donc pio2 en premier, obtient SM0 -- celle de swd_phy -- et y
 * installe son programme SPI. La collision est totale et parfaitement
 * silencieuse : tout fonctionne jusqu'a la premiere commande SWD.
 *
 * D'ou cette fonction, a appeler AVANT toute initialisation susceptible
 * d'allouer des ressources PIO (cyw43_arch_init en particulier). Apres elle,
 * le pilote CYW43 se rabat sur pio2 SM2 et ses 6 mots d'instructions tiennent
 * dans les 9 restants de PIO2.
 *
 * Etat des lieux (mots d'instructions sur 32 par PIO) :
 *   PIO0  32/32  pulse_generator 16 + gpio_edge_detect 9 + irq_trigger 6 ou
 *                uart_rx_decoder 16 -- PLEIN, d'ou le dechargement dynamique
 *                de glitch.c
 *   PIO1  31/32  clock_generator*
 *   PIO2  23/32  swd_phy 11 + swd_phy_nrst 12 -- les 9 mots libres sont la
 *                seule place disponible de toute la puce
 */

#include "pio_alloc.h"
#include "swd_phy.h"
#include "hardware/pio.h"

void pio_resources_reserve(void) {
    // PIO0 : moteur de glitch (glitch.c)
    //   SM0 detection de front / SM1 generateur d'impulsion
    //   SM2 flag ou decodeur UART (jamais les deux en meme temps)
    //   SM3 grille du crowbar
    pio_sm_claim(pio0, 0);
    pio_sm_claim(pio0, 1);
    pio_sm_claim(pio0, 2);
    pio_sm_claim(pio0, 3);

    // PIO1 : generateur d'horloge (PIO separee pour ne pas deborder PIO0)
    pio_sm_claim(pio1, 0);

    // PIO2 : couche physique SWD (swd_phy.c) -- SM0 protocole, SM1 nRST
    pio_sm_claim(pio2, 0);
    pio_sm_claim(pio2, 1);

    // Et charger tout de suite les deux programmes, pour que l'espace
    // instruction de PIO2 soit pris avant qu'un autre pilote ne le convoite
    // (et pour sortir le pio_add_program du chemin chronometre de SWD RACE).
    swd_phy_preload_programs();
}
