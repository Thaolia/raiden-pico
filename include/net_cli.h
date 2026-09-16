#ifndef NET_CLI_H
#define NET_CLI_H

/*
 * CLI reseau (TCP) et discipline temps reel associee.
 *
 * Ce header est inclus INCONDITIONNELLEMENT, y compris sur les cartes sans
 * Wi-Fi : hors RAIDEN_HAS_WIFI, tout se reduit a des fonctions vides que le
 * compilateur elimine. C'est ce qui permet d'ecrire les sites d'appel une
 * seule fois, sans #ifdef dans le code metier, et de garder les trois
 * variantes de carte compilables.
 */

#include <stdbool.h>
#include <stdint.h>

#if RAIDEN_HAS_WIFI

// Initialise la pile reseau, le serveur TCP et le pilote stdio associe.
bool net_cli_init(void);

// A appeler a chaque tour de la boucle principale : c'est le SEUL endroit ou
// lwIP et le pilote CYW43 travaillent. Retourne immediatement si une section
// silencieuse est ouverte.
void net_cli_poll(void);

// Point de relance a placer dans les boucles longues (sweeps, attentes) pour
// que le lien TCP ne meure pas pendant une commande de plusieurs minutes.
void net_cli_pump(void);

// sleep_ms() qui rend la main au reseau, par tranches de 1 ms.
void net_sleep_ms(uint32_t ms);

// --- Sections silencieuses --------------------------------------------------
//
// Pendant une fenetre critique, le reseau doit se taire COMPLETEMENT. Meme en
// mode poll, le pilote CYW43 arme une interruption GPIO de niveau sur son
// host-wake ; or target_uart.c documente, mesures a l'appui, que quelques
// instructions de dispatch d'interruption suffisent a faire rater la fenetre
// de restauration du rail dans la boucle ADC.
//
// net_quiet_enter() masque cette interruption -- CETTE broche uniquement,
// jamais IO_IRQ_BANK0 en entier, qui porte aussi nRST et GLITCH_FIRED -- et
// suspend le poll. L'interruption etant sur NIVEAU, elle se redeclenche
// d'elle-meme au reamorcage : rien n'est perdu, tout au plus retarde.
//
// Le compteur autorise l'imbrication. Preferer la macro NET_QUIET_SECTION,
// qui garantit la sortie sur tous les chemins.
void net_quiet_enter(void);
void net_quiet_exit(void);

#else  // pas de Wi-Fi : tout disparait

static inline bool net_cli_init(void)        { return false; }
static inline void net_cli_poll(void)        { }
static inline void net_cli_pump(void)        { }
static inline void net_quiet_enter(void)     { }
static inline void net_quiet_exit(void)      { }
void net_sleep_ms(uint32_t ms);   // defini dans net_stub.c : simple sleep_ms

#endif // RAIDEN_HAS_WIFI

// Ouvre une section silencieuse jusqu'a la fin du bloc courant, quelle que
// soit la maniere d'en sortir (return, break, goto). Une sortie oubliee
// laisserait le Wi-Fi muet sans que rien ne le signale, d'ou l'attribut
// cleanup plutot qu'un appel apparie a la main.
#define NET_QUIET_CONCAT_(a, b) a##b
#define NET_QUIET_NAME_(line) NET_QUIET_CONCAT_(net_quiet_guard_, line)
static inline void net_quiet_cleanup_(int *unused) { (void)unused; net_quiet_exit(); }
#define NET_QUIET_SECTION                                                   \
    __attribute__((cleanup(net_quiet_cleanup_)))                            \
    int NET_QUIET_NAME_(__LINE__) = (net_quiet_enter(), 0)

#endif // NET_CLI_H
