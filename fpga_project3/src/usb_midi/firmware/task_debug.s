	.attribute	4, 16
	.attribute	5, "rv32i2p1"
	.file	"task.c"
	.text
	.globl	root_reset                      # -- Begin function root_reset
	.p2align	2
	.type	root_reset,@function
root_reset:                             # @root_reset
	.cfi_startproc
# %bb.0:
	lui	a0, %hi(usbh)
	lw	a0, %lo(usbh)(a0)
	li	a1, 196
	sw	a1, 0(a0)
	ret
.Lfunc_end0:
	.size	root_reset, .Lfunc_end0-root_reset
	.cfi_endproc
                                        # -- End function
	.globl	root_config                     # -- Begin function root_config
	.p2align	2
	.type	root_config,@function
root_config:                            # @root_config
	.cfi_startproc
# %bb.0:
	li	a2, 3
	beq	a0, a2, .LBB1_2
# %bb.1:
	li	a2, 496
	j	.LBB1_3
.LBB1_2:
	li	a2, 504
.LBB1_3:
	li	a3, 1
	bne	a0, a3, .LBB1_5
# %bb.4:
	li	a2, 488
.LBB1_5:
	lui	a0, %hi(usbh)
	lw	a0, %lo(usbh)(a0)
	snez	a1, a1
	or	a1, a2, a1
	sw	a1, 0(a0)
	ret
.Lfunc_end1:
	.size	root_config, .Lfunc_end1-root_config
	.cfi_endproc
                                        # -- End function
	.globl	new_task                        # -- Begin function new_task
	.p2align	2
	.type	new_task,@function
new_task:                               # @new_task
	.cfi_startproc
# %bb.0:
	lui	a0, %hi(.L_MergedGlobals)
	addi	a0, a0, %lo(.L_MergedGlobals)
	lbu	a1, 32(a0)
	andi	a1, a1, 3
	beqz	a1, .LBB2_2
# %bb.1:
	addi	sp, sp, -16
	.cfi_def_cfa_offset 16
	sw	ra, 12(sp)                      # 4-byte Folded Spill
	.cfi_offset ra, -4
	lui	a0, %hi(.L.str)
	addi	a0, a0, %lo(.L.str)
	call	printf
	li	a0, 0
	lw	ra, 12(sp)                      # 4-byte Folded Reload
	.cfi_restore ra
	addi	sp, sp, 16
	.cfi_def_cfa_offset 0
	ret
.LBB2_2:
	addi	a0, a0, 32
	ret
.Lfunc_end2:
	.size	new_task, .Lfunc_end2-new_task
	.cfi_endproc
                                        # -- End function
	.globl	clr_task                        # -- Begin function clr_task
	.p2align	2
	.type	clr_task,@function
clr_task:                               # @clr_task
	.cfi_startproc
# %bb.0:
	addi	sp, sp, -16
	.cfi_def_cfa_offset 16
	sw	ra, 12(sp)                      # 4-byte Folded Spill
	sw	s0, 8(sp)                       # 4-byte Folded Spill
	sw	s1, 4(sp)                       # 4-byte Folded Spill
	.cfi_offset ra, -4
	.cfi_offset s0, -8
	.cfi_offset s1, -12
	mv	s0, a0
	lw	s1, 36(a0)
	li	a2, 40
	li	a1, 0
	call	memset
	li	a2, 24
	mv	a0, s1
	li	a1, 0
	call	memset
	li	a0, 8
	sw	s0, 0(s1)
	sh	a0, 14(s1)
	sw	s1, 36(s0)
	mv	a0, s0
	lw	ra, 12(sp)                      # 4-byte Folded Reload
	lw	s0, 8(sp)                       # 4-byte Folded Reload
	lw	s1, 4(sp)                       # 4-byte Folded Reload
	.cfi_restore ra
	.cfi_restore s0
	.cfi_restore s1
	addi	sp, sp, 16
	.cfi_def_cfa_offset 0
	ret
.Lfunc_end3:
	.size	clr_task, .Lfunc_end3-clr_task
	.cfi_endproc
                                        # -- End function
	.globl	main                            # -- Begin function main
	.p2align	2
	.type	main,@function
main:                                   # @main
	.cfi_startproc
# %bb.0:
	addi	sp, sp, -64
	.cfi_def_cfa_offset 64
	sw	ra, 60(sp)                      # 4-byte Folded Spill
	sw	s0, 56(sp)                      # 4-byte Folded Spill
	sw	s1, 52(sp)                      # 4-byte Folded Spill
	sw	s2, 48(sp)                      # 4-byte Folded Spill
	sw	s3, 44(sp)                      # 4-byte Folded Spill
	sw	s4, 40(sp)                      # 4-byte Folded Spill
	sw	s5, 36(sp)                      # 4-byte Folded Spill
	sw	s6, 32(sp)                      # 4-byte Folded Spill
	sw	s7, 28(sp)                      # 4-byte Folded Spill
	sw	s8, 24(sp)                      # 4-byte Folded Spill
	sw	s9, 20(sp)                      # 4-byte Folded Spill
	sw	s10, 16(sp)                     # 4-byte Folded Spill
	sw	s11, 12(sp)                     # 4-byte Folded Spill
	.cfi_offset ra, -4
	.cfi_offset s0, -8
	.cfi_offset s1, -12
	.cfi_offset s2, -16
	.cfi_offset s3, -20
	.cfi_offset s4, -24
	.cfi_offset s5, -28
	.cfi_offset s6, -32
	.cfi_offset s7, -36
	.cfi_offset s8, -40
	.cfi_offset s9, -44
	.cfi_offset s10, -48
	.cfi_offset s11, -52
	lui	a1, %hi(_end-268435456)
	addi	a1, a1, %lo(_end-268435456)
	lui	a0, %hi(.L.str.1)
	addi	a0, a0, %lo(.L.str.1)
	call	printf
	call	is_sim
	lui	s9, %hi(.L_MergedGlobals)
	addi	s9, s9, %lo(.L_MergedGlobals)
	sw	a0, 4(s9)
	addi	a0, s9, 8
	sw	a0, 68(s9)
	addi	s0, s9, 32
	mv	a0, s0
	call	clr_task
	lbu	a0, 32(s9)
	andi	a0, a0, 3
	bnez	a0, .LBB4_29
# %bb.1:
	li	s10, 5
	sw	s10, 32(s9)
	call	now_ms
	mv	s2, a0
	li	s11, 1000
	lui	s1, %hi(.L.str.2)
	addi	s1, s1, %lo(.L.str.2)
	lui	s5, %hi(usbh)
	lui	s6, %hi(.L_MergedGlobals)
	li	s7, 1
	li	s3, 488
	addi	s4, s6, %lo(.L_MergedGlobals)
.LBB4_2:                                # =>This Inner Loop Header: Depth=1
	call	now_ms
	mv	s8, a0
	sub	a0, a0, s2
	bltu	a0, s11, .LBB4_4
# %bb.3:                                #   in Loop: Header=BB4_2 Depth=1
	mv	a0, s1
	call	printf
	mv	s2, s8
.LBB4_4:                                #   in Loop: Header=BB4_2 Depth=1
	lw	a0, 32(s9)
	andi	a1, a0, 3
	beqz	a1, .LBB4_2
# %bb.5:                                #   in Loop: Header=BB4_2 Depth=1
	andi	a1, a0, 1
	beqz	a1, .LBB4_22
# %bb.6:                                #   in Loop: Header=BB4_2 Depth=1
	lw	a1, %lo(usbh)(s5)
	lw	a1, 4(a1)
	andi	a2, a1, 8
	bnez	a2, .LBB4_10
# %bb.7:                                #   in Loop: Header=BB4_2 Depth=1
	lbu	a0, %lo(.L_MergedGlobals)(s6)
	bnez	a0, .LBB4_9
# %bb.8:                                #   in Loop: Header=BB4_2 Depth=1
	lbu	a1, 38(s4)
	lui	a0, %hi(.L.str.3)
	addi	a0, a0, %lo(.L.str.3)
	call	printf
	sb	s7, %lo(.L_MergedGlobals)(s6)
.LBB4_9:                                #   in Loop: Header=BB4_2 Depth=1
	li	a0, 0
	call	midi_set_connected
	mv	a0, s0
	call	clr_task
	lw	a0, %lo(usbh)(s5)
	sw	s3, 0(a0)
	sw	s10, 32(s9)
	j	.LBB4_2
.LBB4_10:                               #   in Loop: Header=BB4_2 Depth=1
	andi	a2, a0, 96
	sb	zero, %lo(.L_MergedGlobals)(s6)
	bnez	a2, .LBB4_22
# %bb.11:                               #   in Loop: Header=BB4_2 Depth=1
	andi	a2, a0, 12
	li	a3, 4
	bne	a2, a3, .LBB4_13
# %bb.12:                               #   in Loop: Header=BB4_2 Depth=1
	andi	a1, a1, 1
	addi	a0, a0, 8
	li	a2, 2
	sub	a1, a2, a1
	sw	a0, 32(s9)
	sb	a1, 36(s9)
	lui	a0, %hi(.L.str.4)
	addi	a0, a0, %lo(.L.str.4)
	call	printf
	li	a0, 20
	call	wait_ms
	lw	a0, 32(s9)
.LBB4_13:                               #   in Loop: Header=BB4_2 Depth=1
	andi	a1, a0, 24
	li	a2, 8
	bne	a1, a2, .LBB4_20
# %bb.14:                               #   in Loop: Header=BB4_2 Depth=1
	lui	a0, %hi(.L.str.5)
	addi	a0, a0, %lo(.L.str.5)
	call	printf
	lw	a0, %lo(usbh)(s5)
	li	a1, 196
	sw	a1, 0(a0)
	call	reset_enum
	li	a0, 50
	call	wait_ms
	lbu	a1, 36(s9)
	li	a0, 3
	beq	a1, a0, .LBB4_16
# %bb.15:                               #   in Loop: Header=BB4_2 Depth=1
	li	a0, 496
	j	.LBB4_17
.LBB4_16:                               #   in Loop: Header=BB4_2 Depth=1
	li	a0, 504
.LBB4_17:                               #   in Loop: Header=BB4_2 Depth=1
	lw	a2, 4(s9)
	bne	a1, s7, .LBB4_19
# %bb.18:                               #   in Loop: Header=BB4_2 Depth=1
	li	a0, 488
.LBB4_19:                               #   in Loop: Header=BB4_2 Depth=1
	lw	a1, %lo(usbh)(s5)
	seqz	a2, a2
	or	a0, a0, a2
	sw	a0, 0(a1)
	lw	a0, 32(s9)
	ori	a0, a0, 16
	sw	a0, 32(s9)
	lui	a0, %hi(.L.str.6)
	addi	a0, a0, %lo(.L.str.6)
	call	printf
	lw	a0, 32(s9)
.LBB4_20:                               #   in Loop: Header=BB4_2 Depth=1
	andi	a1, a0, 48
	li	a2, 16
	bne	a1, a2, .LBB4_22
# %bb.21:                               #   in Loop: Header=BB4_2 Depth=1
	li	a0, 100
	call	wait_ms
	lw	a0, 32(s9)
	ori	a0, a0, 32
	sw	a0, 32(s9)
	sb	zero, 39(s9)
	lui	a0, %hi(enum_dev)
	addi	a0, a0, %lo(enum_dev)
	sw	a0, 48(s9)
	lui	a0, %hi(.L.str.7)
	addi	a0, a0, %lo(.L.str.7)
	call	printf
	lw	a0, 32(s9)
.LBB4_22:                               #   in Loop: Header=BB4_2 Depth=1
	andi	a0, a0, 32
	beqz	a0, .LBB4_2
# %bb.23:                               #   in Loop: Header=BB4_2 Depth=1
	lw	a0, 68(s9)
	lbu	a1, 9(a0)
	beqz	a1, .LBB4_25
# %bb.24:                               #   in Loop: Header=BB4_2 Depth=1
	call	do_request_step
	j	.LBB4_2
.LBB4_25:                               #   in Loop: Header=BB4_2 Depth=1
	lw	a0, 4(s9)
	bnez	a0, .LBB4_27
# %bb.26:                               #   in Loop: Header=BB4_2 Depth=1
	lw	s8, 40(s9)
	call	now_ms
	bltu	a0, s8, .LBB4_2
.LBB4_27:                               #   in Loop: Header=BB4_2 Depth=1
	lw	a2, 48(s9)
	mv	a0, s0
	li	a1, 0
	jalr	a2
	lbu	a0, 39(s9)
	li	a1, 255
	bne	a0, a1, .LBB4_2
# %bb.28:                               #   in Loop: Header=BB4_2 Depth=1
	lw	a0, 32(s9)
	ori	a0, a0, 64
	sw	a0, 32(s9)
	j	.LBB4_2
.LBB4_29:
	lui	a0, %hi(.L.str)
	addi	a0, a0, %lo(.L.str)
	call	printf
.Lfunc_end4:
	.size	main, .Lfunc_end4-main
	.cfi_endproc
                                        # -- End function
	.type	usbh,@object                    # @usbh
	.data
	.globl	usbh
	.p2align	2, 0x0
usbh:
	.word	553648128
	.size	usbh, 4

	.type	.L.str,@object                  # @.str
	.section	.rodata.str1.1,"aMS",@progbits,1
.L.str:
	.asciz	"panic: out of tasks\n"
	.size	.L.str, 21

	.type	.L.str.1,@object                # @.str.1
.L.str.1:
	.asciz	"Primer25K USB-MIDI host, firmware=%x bytes\n"
	.size	.L.str.1, 44

	.type	.L.str.2,@object                # @.str.2
.L.str.2:
	.asciz	"USB host alive\n"
	.size	.L.str.2, 16

	.type	.L.str.3,@object                # @.str.3
.L.str.3:
	.asciz	"device disconnected, task addr = %d\n"
	.size	.L.str.3, 37

	.type	.L.str.4,@object                # @.str.4
.L.str.4:
	.asciz	"device connect, speed = %x\n"
	.size	.L.str.4, 28

	.type	.L.str.5,@object                # @.str.5
.L.str.5:
	.asciz	"USB root reset begin\n"
	.size	.L.str.5, 22

	.type	.L.str.6,@object                # @.str.6
.L.str.6:
	.asciz	"USB root reset released\n"
	.size	.L.str.6, 25

	.type	.L.str.7,@object                # @.str.7
.L.str.7:
	.asciz	"USB root enabled\n"
	.size	.L.str.7, 18

	.type	.L_MergedGlobals,@object        # @_MergedGlobals
	.local	.L_MergedGlobals
	.comm	.L_MergedGlobals,72,4
	.globl	donotspam
donotspam = .L_MergedGlobals
	.size	donotspam, 1
	.globl	sim
sim = .L_MergedGlobals+4
	.size	sim, 4
	.globl	requests
requests = .L_MergedGlobals+8
	.size	requests, 24
	.globl	tasks
tasks = .L_MergedGlobals+32
	.size	tasks, 40
	.ident	"clang version 21.1.0"
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.addrsig_sym enum_dev
	.addrsig_sym _end
	.addrsig_sym .L_MergedGlobals
