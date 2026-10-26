# Zephyr Modbus RTU server firmware

This binary is built from Zephyr v4.2.0, commit `413b789deb391d3a37d06b463288a5fe765ee57e`, `samples/subsys/modbus/rtu_server`, for `stm32f4_disco`. The application and Modbus stack are unchanged. The included board overlay selects USART2, and extra configuration disables console/log output on that UART. The original project is <https://github.com/zephyrproject-rtos/zephyr>.

Upstream application copyrights include PHYTEC Messtechnik GmbH (2020) and Nordic Semiconductor ASA (2022). The Modbus server retains Silicon Laboratories Inc. (2003–2020) notices. Zephyr and CMSIS use Apache-2.0; ST's STM32F4 component uses BSD-3-Clause. Preserve the license texts and compiled-source notices in `licenses/` when redistributing this firmware.

Pinned modules:

| Module | Repository | Commit |
|---|---|---|
| CMSIS | https://github.com/zephyrproject-rtos/cmsis | `512cc7e895e8491696b61f7ba8066b4a182569b8` |
| CMSIS 6 | https://github.com/zephyrproject-rtos/CMSIS_6 | `06d952b6713a2ca41c9224a62075e4059402a151` |
| STM32 HAL | https://github.com/zephyrproject-rtos/hal_stm32 | `1e753266ddfb4b07a8a0b1ec566e9637ea45d5ef` |

The build uses Arm GNU Toolchain 14.2.Rel1, including libgcc under GPLv3 with the GCC Runtime Library Exception 3.1. Both texts are included. These component licenses do not change the project's own license. No proprietary ST binary blobs were fetched for this image.

The firmware is public input; private reference drivers and qualification episodes are distributed only in the encrypted evaluator bundle. The evaluator password is a separate operator credential and is not included in this distribution. See the repository's `docs/benchmark-corpus.md` for source reconstruction and qualification commands.

The separate host transport adapter in `platforms/BufferedSocketTerminal.cs` is derived from Renode's `ServerSocketTerminal`, copyright Antmicro (2010–2026) and Realtime Embedded (2011–2015), under MIT. Its license is in `platforms/Renode-MIT.txt`. It is not linked into this firmware.
