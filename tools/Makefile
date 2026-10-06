# Boot trampolines (see trampoline.S): DTB right after the code (TFTP boots)
# or at +0x1000 inside a flash slot.
CROSS_COMPILE ?= arm-none-eabi-

all: trampoline.bin trampoline-flash.bin

trampoline.bin: DEFS =
trampoline-flash.bin: DEFS = -DDTB_ADDR=0x80F01000

%.bin: trampoline.S trampoline.lds
	$(CROSS_COMPILE)gcc -march=armv7-a $(DEFS) -c trampoline.S -o $*.o
	$(CROSS_COMPILE)ld -T trampoline.lds $*.o -o $*.elf
	$(CROSS_COMPILE)objcopy -O binary $*.elf $@

clean:
	rm -f *.o *.elf *.bin

.PHONY: all clean
