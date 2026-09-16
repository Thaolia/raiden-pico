/*
 * Configuration lwIP pour raiden-pico (variante Pico 2 W).
 *
 * Ce fichier n'est lu que par la variante Wi-Fi ; il est inoffensif pour les
 * builds pico2 / xxl, qui ne lient pas lwIP.
 *
 * Deux choix meritent une justification :
 *
 *   NO_SYS = 1 -- il n'y a pas d'ordonnanceur dans ce firmware. La boucle de
 *   main.c est deja un modele cooperatif, et l'API "raw" de lwIP est faite
 *   pour cela. LWIP_SOCKET/LWIP_NETCONN exigeraient un RTOS.
 *
 *   Les tailles de fenetre et de file d'emission sont dimensionnees pour les
 *   GROS transferts, pas pour la ligne de commande : un `SWD MEMREAD` de 4 Ko
 *   emet ~12,8 Ko de texte, et un dump complet plusieurs centaines de Ko. Une
 *   file d'emission trop courte ferait echouer le drain en ligne du pilote
 *   stdio, qui est ce qui rend ces transferts possibles sans perte.
 */
#ifndef _LWIPOPTS_H
#define _LWIPOPTS_H

#define NO_SYS                      1
#define LWIP_SOCKET                 0
#define LWIP_NETCONN                0

#define MEM_LIBC_MALLOC             0
#define MEM_ALIGNMENT               4
#define MEM_SIZE                    16000
#define MEMP_NUM_TCP_SEG            32
#define MEMP_NUM_ARP_QUEUE          10
#define PBUF_POOL_SIZE              24

#define LWIP_ARP                    1
#define LWIP_ETHERNET               1
#define LWIP_ICMP                   1   /* ping : premier diagnostic utile   */
#define LWIP_RAW                    1
#define LWIP_TCP                    1
#define LWIP_UDP                    1   /* requis par DHCP                   */
#define LWIP_DHCP                   1
#define LWIP_DNS                    0
#define LWIP_IPV6                   0

#define TCP_MSS                     1460
#define TCP_WND                     (8 * TCP_MSS)
#define TCP_SND_BUF                 (8 * TCP_MSS)
#define TCP_SND_QUEUELEN            ((4 * (TCP_SND_BUF) + (TCP_MSS - 1)) / (TCP_MSS))
#define TCP_LISTEN_BACKLOG          1

/* Sans keepalive, un PC en veille ou debranche laisse le firmware croire a un
 * client vivant -- donc potentiellement le glitcher arme, sans surveillance. */
#define LWIP_TCP_KEEPALIVE          1

#define LWIP_NETIF_STATUS_CALLBACK  1
#define LWIP_NETIF_LINK_CALLBACK    1
#define LWIP_NETIF_HOSTNAME         1
#define DHCP_DOES_ARP_CHECK         0
#define LWIP_DHCP_DOES_ACD_CHECK    0
#define LWIP_CHKSUM_ALGORITHM       3
#define LWIP_STATS                  0
#define SYS_LIGHTWEIGHT_PROT        0   /* NO_SYS=1 : un seul contexte       */

#endif /* _LWIPOPTS_H */
