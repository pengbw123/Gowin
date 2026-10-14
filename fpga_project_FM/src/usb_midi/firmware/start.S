    .section .text.start
    .global _start
    .type _start, @function

_start:
    li sp, 0x10004000

    la a0, _sbss
    la a1, _ebss
1:
    bgeu a0, a1, 2f
    sw zero, 0(a0)
    addi a0, a0, 4
    j 1b
2:
    call main
3:
    j 3b

