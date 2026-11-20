# Breakdown: read an STM32's firmware off the chip in the acquire stage

This Run is for an operator who starts a run on an STM32 device and has no firmware file. Today the acquire stage can read a chip only when it is an ESP32. After this Run the operator names an ST-LINK probe with the run's device, and the acquire worker reads the chip's flash through STM32CubeProgrammer and hands the image to interpret.

The tool only reads. It never writes, erases or removes a chip's read protection. It reaches the chip through an ST-LINK probe over SWD; the serial and USB bootloader connections of STM32CubeProgrammer, and other probes, are not part of this Run. STM32CubeProgrammer is the operator's own installation: this repository neither ships nor downloads it. The Run's tests use a stand-in program, so the Run gives no physical evidence; the first read of a real chip is the operator's to run.

01. stm32-flash-read — a new acquire tool reads an STM32's flash through STM32CubeProgrammer and a declared ST-LINK probe, read-only, and hands the image to interpret
