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

// Board-aware status LED.
// On the base Pico 2 (and XXL) the SDK board header defines PICO_DEFAULT_LED_PIN
// (GP25 on Pico 2) and we drive it directly. On the Pico 2 W the onboard LED
// hangs off the CYW43 wireless chip, so PICO_DEFAULT_LED_PIN is undefined there;
// we no-op instead of driving GP25, which on the W is the CYW43 SPI chip-select
// (WL_CS). This keeps the LED working on Pico 2 / XXL without pulling in the
// wireless stack, and avoids a pin conflict on the Pico 2 W.
#ifdef PICO_DEFAULT_LED_PIN
static inline void status_led_init(void) {
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
    gpio_put(PICO_DEFAULT_LED_PIN, 1);  // Turn on LED
}
static inline void status_led_set(bool on) { gpio_put(PICO_DEFAULT_LED_PIN, on); }
#else
static inline void status_led_init(void) { }        // Pico 2 W: LED is on CYW43, no GPIO
static inline void status_led_set(bool on) { (void)on; }
#endif

int main() {
    // Initialize standard I/O
    stdio_init_all();

    // Small delay for USB serial to stabilize
    sleep_ms(2000);

    // Send early test message
    printf("Raiden Pico starting...\n");

    // Reserver les ressources PIO cablees en dur AVANT toute initialisation
    // susceptible d'en allouer. Precharge aussi les programmes swd_phy, pour
    // sortir pio_add_program du chemin chronometre de SWD RACE. Voir
    // src/pio_alloc.c.
    pio_resources_reserve();

    // Abstraction de sortie CLI : no-op hors Wi-Fi (backend TCP non compile,
    // RAIDEN_HAS_WIFI jamais defini sur les cibles actuelles).
    net_cli_init();

    // Initialize LED (board-aware; no-op on Pico 2 W)
    status_led_init();

    printf("LED initialized\n");

    // Initialize subsystems one by one with debug output
    printf("Initializing command parser...\n");
    command_parser_init();

    printf("Initializing UART CLI...\n");
    uart_cli_init();

    printf("Initializing glitch...\n");
    glitch_init();

    // UART0 (GP0/GP1) appartient soit au ChipSHOUTER, soit a la console UART
    // -- jamais aux deux. Voir l'option RAIDEN_CONSOLE_UART du CMakeLists.
#if RAIDEN_CONSOLE_UART
    printf("Console UART0 GP0/GP1 active - ChipShouter UART disabled\n");
#else
    printf("Initializing ChipShouter UART...\n");
    chipshot_uart_init();
#endif

    printf("Initializing target subsystem...\n");
    target_init();

    printf("All systems initialized!\n");

    // Blink LED to indicate ready
    for (int i = 0; i < 3; i++) {
        status_led_set(false);
        sleep_ms(100);
        status_led_set(true);
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

        // Process ChipShouter UART -- muet quand la console possede UART0,
        // sinon il consommerait les octets tapes par l'operateur.
#if !RAIDEN_CONSOLE_UART
        chipshot_uart_process();
#endif

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
