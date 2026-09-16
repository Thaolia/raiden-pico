cmake_minimum_required(VERSION 3.13)

# Board variant selection: BOARD=pico2 (default) or BOARD=xxl (Olimex RP2350-XXL)
set(BOARD "pico2" CACHE STRING "Board variant: pico2 or xxl")
if(BOARD STREQUAL "xxl")
    set(PICO_BOARD olimex_rp2350_xxl CACHE STRING "Board type" FORCE)
    set(BOARD_NAME "Olimex RP2350-XXL")
else()
    set(PICO_BOARD pico2 CACHE STRING "Board type" FORCE)
    set(BOARD_NAME "Raspberry Pi Pico 2")
endif()
message(STATUS "Building for ${BOARD_NAME} (PICO_BOARD=${PICO_BOARD})")

# Console CLI sur UART0 (GP0/GP1) EN PLUS de l'USB.
#
# Le pilote stdio du SDK multiplexe les deux : rien a changer dans uart_cli.c,
# la CLI repond simplement sur les deux liens. Sert a faire passer la console
# par un pont USB-UART externe (le FaultyCat expose un uart_passthrough sur
# CH0/CH1 -> CDC3).
#
# ATTENTION -- exclusion mutuelle materielle : UART0 GP0/GP1 est la liaison
# ChipSHOUTER. Quand cette option est active, chipshot_uart_{init,process}()
# ne sont PAS appeles et la commande CS refuse explicitement de s'executer :
# les deux ne peuvent pas posseder le meme peripherique.
#
# OFF par defaut : le binaire pico2 par defaut ne change pas.
option(RAIDEN_CONSOLE_UART "CLI aussi sur UART0 GP0/GP1 (exclut le ChipSHOUTER)" OFF)
if(RAIDEN_CONSOLE_UART)
    message(STATUS "Console UART0 GP0/GP1 ACTIVE -- ChipSHOUTER desactive")
endif()

# Pull in SDK (must be before project)
include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(raiden_pico C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

# Initialize the SDK
pico_sdk_init()

# Main executable
add_executable(raiden_pico
    src/main.c
    src/pio_alloc.c
    src/net_stub.c
    src/uart_cli.c
    src/command_parser.c
    src/glitch.c
    src/chipshot_uart.c
    src/target_uart.c
    src/grbl.c
    src/swd.c
    src/swd_phy.c
    src/jtag.c
    src/stm32_target.c
    src/stm32_breakpoints.c
    src/lpc_target.c
    src/bat32_target.c
)

# Include directories
target_include_directories(raiden_pico PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/include
)

# Generate PIO headers
pico_generate_pio_header(raiden_pico ${CMAKE_CURRENT_LIST_DIR}/src/glitch.pio)
pico_generate_pio_header(raiden_pico ${CMAKE_CURRENT_LIST_DIR}/src/swd_phy.pio)
# grbl.pio not used - Grbl now uses hardware UART1 on GP8/GP9 (alternate function)
# pico_generate_pio_header(raiden_pico ${CMAKE_CURRENT_LIST_DIR}/src/grbl.pio)
# uart.pio not used - target UART uses hardware UART (uart1), not PIO
# pico_generate_pio_header(raiden_pico ${CMAKE_CURRENT_LIST_DIR}/src/uart.pio)

# Link libraries
target_link_libraries(raiden_pico
    pico_stdlib
    hardware_pio
    hardware_uart
    hardware_pwm
    hardware_gpio
    hardware_timer
    hardware_adc
    hardware_dma
)

# Sortie serie : USB toujours, UART0 seulement sur demande (voir l'option en
# tete de fichier). pico_enable_stdio_uart(1) utilise PICO_DEFAULT_UART, qui
# est uart0 sur GP0/GP1 pour la Pico 2 -- exactement les broches ChipSHOUTER.
pico_enable_stdio_usb(raiden_pico 1)
if(RAIDEN_CONSOLE_UART)
    pico_enable_stdio_uart(raiden_pico 1)
    target_compile_definitions(raiden_pico PRIVATE RAIDEN_CONSOLE_UART=1)
else()
    pico_enable_stdio_uart(raiden_pico 0)
endif()

# Create map/bin/hex/uf2 files
pico_add_extra_outputs(raiden_pico)

# Add flash target
add_custom_target(flash
    COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/scripts/flash.sh ${CMAKE_CURRENT_BINARY_DIR}
    DEPENDS raiden_pico
    COMMENT "Flashing firmware to ${BOARD_NAME}"
    WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}
)
