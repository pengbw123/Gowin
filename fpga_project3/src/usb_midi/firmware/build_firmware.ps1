param(
    [string]$Zig = $env:ZIG_EXE
)

$ErrorActionPreference = 'Stop'
$firmwareDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $Zig) {
    $zigCommand = Get-Command zig -ErrorAction SilentlyContinue
    if ($zigCommand) {
        $Zig = $zigCommand.Source
    }
}
if (-not $Zig -or -not (Test-Path -LiteralPath $Zig)) {
    throw 'zig executable not found. Pass -Zig <path-to-zig.exe> or set ZIG_EXE.'
}

$sources = @(
    'start.S',
    'task.c',
    'req.c',
    'enum.c',
    'midi.c',
    'prnt.c',
    'lib.c'
) | ForEach-Object { Join-Path $firmwareDir $_ }

$elf = Join-Path $firmwareDir 'firmware.elf'
$binary = Join-Path $firmwareDir 'firmware.bin'
$hex = Join-Path $firmwareDir 'firmware.hex'

# This soft core implements RV32I only.  Zig's baseline_rv32 also enables
# A/C/D/M, which allowed 16-bit compressed and multiply instructions into the
# image and made the CPU lose instruction alignment during startup.
& $Zig cc `
    -target riscv32-freestanding-none `
    -mcpu=generic_rv32 `
    -mabi=ilp32 `
    -Os `
    -ffreestanding `
    -fno-builtin `
    -fno-pic `
    -fno-stack-protector `
    -fdata-sections `
    -ffunction-sections `
    -msmall-data-limit=0 `
    -nostdlib `
    "-Wl,-T,$(Join-Path $firmwareDir 'linker.ld')" `
    '-Wl,--gc-sections' `
    '-Wl,--build-id=none' `
    @sources `
    -o $elf
if ($LASTEXITCODE -ne 0) { throw 'Firmware link failed.' }

& $Zig objcopy -O binary $elf $binary
if ($LASTEXITCODE -ne 0) { throw 'Firmware objcopy failed.' }

$bytes = [System.IO.File]::ReadAllBytes($binary)
if ($bytes.Length -gt 16384) {
    throw "Firmware image is $($bytes.Length) bytes, larger than 16 KiB."
}

$lines = [System.Collections.Generic.List[string]]::new(4096)
for ($offset = 0; $offset -lt 16384; $offset += 4) {
    $word = [uint32]0
    for ($byteIndex = 0; $byteIndex -lt 4; $byteIndex++) {
        $index = $offset + $byteIndex
        if ($index -lt $bytes.Length) {
            $word = $word -bor ([uint32]$bytes[$index] -shl (8 * $byteIndex))
        }
    }
    $lines.Add($word.ToString('X8'))
}
[System.IO.File]::WriteAllLines($hex, $lines, [System.Text.Encoding]::ASCII)

Write-Host "USB-MIDI firmware: $($bytes.Length) / 16384 bytes"
Write-Host "Generated: $hex"
