#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "uart_cli.h"
#include "command_parser.h"
#include "glitch.h"
#include "config.h"
#include "pio_alloc.h"
#include "net_cli.h"
#include <stdio.h>
#if RAIDEN_HAS_WIFI
#include "pico/cyw43_arch.h"
#endif

// Forward declarations for UART modules
extern void chipshot_uart_init(void);
extern void chipshot_uart_process(void);
extern void target_uart_process(void);
extern void target_init(void);

// LED d'etat.
//
// ATTENTION : sur Pico 2 W, GP25 n'est PAS la LED, c'est le chip-select du
// bus SPI vers le module Wi-Fi. Le piloter en GPIO tue la liaison CYW43 --
// et comme pico2_w.h ne definit deliberement aucun PICO_DEFAULT_LED_PIN
// ("LED is on Wireless chip"), un `#define LED_PIN 25` en dur compilerait
// sans le moindre avertissement. La LED passe donc par le module Wi-Fi, ce
// qui impose que cyw43_arch_init() ait deja reussi avant tout allumage.
#if defined(CYW43_WL_GPIO_LED_PIN)
  #define RAIDEN_LED_INIT()  ((void)0)
  #define RAIDEN_LED_SET(v)  cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, (v))
#elif defined(PICO_DEFAULT_LED_PIN)
  #define RAIDEN_LED_INIT()  do { gpio_init(PICO_DEFAULT_LED_PIN);          \
                                  gpio_set_dir(PICO_DEFAULT_LED_PIN,        \
                                               GPIO_OUT); } while (0)
  #define RAIDEN_LED_SET(v)  gpio_put(PICO_DEFAULT_LED_PIN, (v))
#else
  #define RAIDEN_LED_INIT()  ((void)0)
  #define RAIDEN_LED_SET(v)  ((void)(v))
#endif

int main() {
    // Initialize standard I/O
    stdio_init_all();

    // Small delay for USB serial to stabilize
    sleep_ms(2000);

    // Send early test message
    printf("Raiden Pico starting...\n");

    // Reserver les ressources PIO cablees en dur AVANT toute initialisation
    // qui pourrait en allouer (cyw43_arch_init au premier chef). Voir
    // src/pio_alloc.c : sans cela le pilote Wi-Fi vole sa state machine a
    // swd_phy, et rien ne le signale jusqu'a la premiere commande SWD.
    pio_resources_reserve();

    // Wi-Fi (Pico 2 W) : apres la reservation PIO, et AVANT le premier
    // allumage de LED -- sur cette carte la LED passe par le module Wi-Fi.
    net_cli_init();

    RAIDEN_LED_INIT();
    RAIDEN_LED_SET(1);

    printf("LED initialized\n");

    // Initialize subsystems one by one with debug output
    printf("Initializing command parser...\n");
    command_parser_init();

    printf("Initializing UART CLI...\n");
    uart_cli_init();

    printf("Initializing glitch...\n");
    glitch_init();

    printf("Initializing ChipShouter UART...\n");
    chipshot_uart_init();

    printf("Initializing target subsystem...\n");
    target_init();

    printf("All systems initialized!\n");

    // Blink LED to indicate ready
    for (int i = 0; i < 3; i++) {
        RAIDEN_LED_SET(0);
        sleep_ms(100);
        RAIDEN_LED_SET(1);
        sleep_ms(100);
    }

    printf("Ready!\n");
    printf("\n");
    printf("========================================\n");
    printf("       GPIO TRIGGER CONFIGURATION      \n");
    printf("========================================\n");
    printf("For GPIO trigger on GP3 (from GP15 RESET):\n");
    printf("  Use DIRECT connection (no resistor)\n");
    printf("  GP3 is input - safe for direct wire\n");
    printf("  Resistor degrades edge detection!\n");
    printf("========================================\n");
    printf("\n");

    // Main loop
    while (true) {
        // Process UART CLI
        uart_cli_process();

        // Check if command is ready
        if (uart_cli_command_ready()) {
            const char *cmd = uart_cli_get_command();

            // Parse command
            cmd_parts_t parts;
            if (command_parser_parse(cmd, &parts)) {
                // Execute command
                command_parser_execute(&parts);
            }

            // Clear command buffer
            uart_cli_clear_command();
        }

        // Process ChipShouter UART
        chipshot_uart_process();

        // Process Target UART
        target_uart_process();

        // Update glitch flags
        glitch_update_flags();

        // Service reseau : seul endroit ou lwIP et le pilote Wi-Fi
        // travaillent. Sans effet (et sans coût) sur une carte sans Wi-Fi.
        net_cli_poll();

        // Small delay to prevent busy waiting
        sleep_us(100);
    }

    return 0;
}
