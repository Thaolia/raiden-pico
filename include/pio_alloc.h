#ifndef PIO_ALLOC_H
#define PIO_ALLOC_H

// Reserve aupres du SDK les 7 state machines que le firmware cable en dur,
// et charge les programmes PIO de la couche physique SWD.
//
// A appeler UNE fois, tot dans main(), et imperativement AVANT toute
// initialisation susceptible d'allouer des ressources PIO -- au premier chef
// cyw43_arch_init(), dont le pilote se servirait sinon dans pio2 et volerait
// sa state machine a swd_phy, silencieusement. Voir src/pio_alloc.c.
void pio_resources_reserve(void);

#endif // PIO_ALLOC_H
