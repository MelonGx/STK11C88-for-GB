#!/usr/bin/env python3
"""
Pokémon Prism (PRISM254.GBC) — STK11C88 SoftStore nvSRAM patch
================================================================
Patches TWO locations:
  1. $3CC7 (file 0x03CC7): inserts nvSTORE routine (64 bytes)
  2. $4D88 (file 0x14D88): redirects jp $0666 → jp $3CC7

After patch, the save flow becomes:
  orchestrator B → ... → $4D88 jp nvSTORE → STORE sequence →
  busy-wait 10ms+ → jp $0666 (CloseSRAM) → ret to caller

Hardware preconditions (on DMG-KFDN board):
  - Cut MM1134 → SRAM-VCC; tie STK11C88 VCC to main 5V only
  - Add 10kΩ pull-up on SRAM /CE (or /WE) to 5V
"""

import sys, hashlib

# ── nvSTORE machine code at $3CC7 (64 bytes) ──
# push af; push bc; push hl; di
# ld a,$0A; ld [$0000],a           ; RAM enable
# xor a;   ld [$4000],a            ; bank 0
# ld a,[$AE38]                     ; STORE read 1 (addr 0E38)
# ld a,$01; ld [$4000],a           ; bank 1
# ld a,[$B1C7]                     ; STORE read 2 (addr 31C7)
# xor a;   ld [$4000],a            ; bank 0
# ld a,[$A3E0]                     ; STORE read 3 (addr 03E0)
# ld a,$01; ld [$4000],a           ; bank 1
# ld a,[$BC1F]                     ; STORE read 4 (addr 3C1F)
# ld a,[$B03F]                     ; STORE read 5 (addr 303F)
# xor a;   ld [$4000],a            ; bank 0
# ld a,[$AFC0]                     ; STORE read 6 (addr 0FC0) → STORE begins
# ld bc,$1F40                      ; wait 8000 iters (~27ms@8MHz)
# .wait: dec bc; ld a,b; or c; jr nz,.wait
# ei; pop hl; pop bc; pop af
# jp $0666                         ; → CloseSRAM (RAM disable + ret)

NVSRAM_CODE = bytes([
    0xF5,                           # push af
    0xC5,                           # push bc
    0xE5,                           # push hl
    0xF3,                           # di
    0x3E, 0x0A,                     # ld a,$0A
    0xEA, 0x00, 0x00,               # ld [$0000],a     ; RAM enable
    0xAF,                           # xor a
    0xEA, 0x00, 0x40,               # ld [$4000],a     ; bank 0
    0xFA, 0x38, 0xAE,               # ld a,[$AE38]     ; (1)
    0x3E, 0x01,                     # ld a,$01
    0xEA, 0x00, 0x40,               # ld [$4000],a     ; bank 1
    0xFA, 0xC7, 0xB1,               # ld a,[$B1C7]     ; (2)
    0xAF,                           # xor a
    0xEA, 0x00, 0x40,               # ld [$4000],a     ; bank 0
    0xFA, 0xE0, 0xA3,               # ld a,[$A3E0]     ; (3)
    0x3E, 0x01,                     # ld a,$01
    0xEA, 0x00, 0x40,               # ld [$4000],a     ; bank 1
    0xFA, 0x1F, 0xBC,               # ld a,[$BC1F]     ; (4)
    0xFA, 0x3F, 0xB0,               # ld a,[$B03F]     ; (5)
    0xAF,                           # xor a
    0xEA, 0x00, 0x40,               # ld [$4000],a     ; bank 0
    0xFA, 0xC0, 0xAF,               # ld a,[$AFC0]     ; (6) STORE trigger
    0x01, 0x40, 0x1F,               # ld bc,$1F40      ; 8000
    0x0B,                           # .wait: dec bc
    0x78,                           # ld a,b
    0xB1,                           # or c
    0x20, 0xFB,                     # jr nz,.wait
    0xFB,                           # ei
    0xE1,                           # pop hl
    0xC1,                           # pop bc
    0xF1,                           # pop af
    0xC3, 0x66, 0x06,               # jp $0666 (CloseSRAM)
])

# Patch site: $4D88 (file 0x14D88) — 3 bytes
HOOK_OFFSET  = 0x14D88
HOOK_ORIG    = bytes([0xC3, 0x66, 0x06])   # jp $0666
HOOK_PATCHED = bytes([0xC3, 0xC7, 0x3C])   # jp $3CC7

# Routine placement: $3CC7 (file 0x03CC7)
CODE_OFFSET  = 0x03CC7

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 patch_prism_nvsram.py PRISM254.GBC [output.gbc]")
        sys.exit(1)

    inpath  = sys.argv[1]
    outpath = sys.argv[2] if len(sys.argv) > 2 else inpath.replace('.GBC','_nvsram.GBC').replace('.gbc','_nvsram.gbc')

    rom = bytearray(open(inpath, 'rb').read())

    # ── Sanity checks ──
    title = rom[0x134:0x143].split(b'\x00')[0]
    assert title == b'PM_PRISM', f"Not Prism: title={title}"
    assert rom[0x147] == 0x10, f"Cart type {rom[0x147]:02X} != MBC3+TIMER+RAM+BAT"
    assert rom[0x148] == 0x06, f"ROM size {rom[0x148]:02X} != 2MB"
    assert rom[0x149] == 0x03, f"RAM size {rom[0x149]:02X} != 32KB"

    # Verify hook site contains original bytes
    assert rom[HOOK_OFFSET:HOOK_OFFSET+3] == HOOK_ORIG, \
        f"Hook site mismatch: expected C3 66 06, got {rom[HOOK_OFFSET:HOOK_OFFSET+3].hex()}"

    # Verify code area is empty (all 0xFF)
    code_area = rom[CODE_OFFSET:CODE_OFFSET+len(NVSRAM_CODE)]
    assert all(b == 0xFF for b in code_area), \
        f"Code area at {CODE_OFFSET:#x} not empty! First non-FF at offset {next(i for i,b in enumerate(code_area) if b!=0xFF)}"

    print(f"Input  : {inpath}")
    print(f"Output : {outpath}")
    print(f"Title  : {title.decode()}")
    print(f"Routine: {len(NVSRAM_CODE)} bytes @ $3CC7 (file {CODE_OFFSET:#06x})")
    print(f"Hook   : $4D88 jp $0666 → jp $3CC7 (file {HOOK_OFFSET:#06x})")

    # ── Apply patches ──
    rom[CODE_OFFSET:CODE_OFFSET+len(NVSRAM_CODE)] = NVSRAM_CODE
    rom[HOOK_OFFSET:HOOK_OFFSET+3] = HOOK_PATCHED

    # ── Fix header checksum ($014D) ──
    cksum = 0
    for i in range(0x134, 0x14D):
        cksum = (cksum - rom[i] - 1) & 0xFF
    rom[0x14D] = cksum

    # ── Fix global checksum ($014E-014F) ──
    rom[0x14E] = 0; rom[0x14F] = 0
    gsum = sum(rom) & 0xFFFF
    rom[0x14E] = (gsum >> 8) & 0xFF
    rom[0x14F] = gsum & 0xFF

    open(outpath, 'wb').write(rom)

    md5 = hashlib.md5(rom).hexdigest()
    print(f"\nDone. MD5: {md5}")
    print(f"\nVerification — read back patched bytes:")
    print(f"  $3CC7: {rom[CODE_OFFSET:CODE_OFFSET+8].hex(' ')}")
    print(f"  $4D88: {rom[HOOK_OFFSET:HOOK_OFFSET+3].hex(' ')}")

if __name__ == '__main__':
    main()
