# CMP 50HX multi-GPU work

## Current behavior

`--discover-only` reads PCI configuration through CF8/CFC and prints every
`10de:1e09` BDF. It does not write PCI configuration, GPU MMIO, GPU firmware,
UEFI variables, or BootOrder/BootNext. It loads the existing OS loader and
chainloads it unless `--return-to-grub` is also supplied. The usual invocation
still unlocks the first card found. `--bdf=BB:DD.F` opts into the existing
single-card unlock path for one discovered card; a BDF that is absent or has a
different device ID is rejected before BAR/MMIO writes. P104-100 devices
(`10de:1b87`) are never selected.

A malformed or repeated `--bdf` option is rejected with
`EFI_INVALID_PARAMETER`; it cannot fall back to the default card. If the
discovery-only chainload fails, its EFI error status is returned to firmware
after it is logged.

`--all` is an explicit, fail-stop sequential mode. It requires exactly eight
discovered CMP 50HX devices, snapshots those BDFs, preloads the OS loader once,
performs an EFI memory-capacity preflight, and then invokes the per-card worker
in BDF discovery order. It does not select P104 devices. `--all` cannot be
combined with `--discover-only` or `--bdf`; conflicting modes are rejected.

## Single-GPU state and lifetime review

| State | Current lifetime / multi-card concern |
|---|---|
| `gRb`, `gBus/gDev/gFn`, `u40x_enc_found` | Global active PCI access target. Must be rebound for each BDF. |
| `gBar0Base` | Global direct MMIO base; currently overwritten only by BAR setup. Must not survive a card switch. |
| `g_fbSize`, `g_frtsOffset`, `g_wpr2LoUp/g_wpr2HiUp` | Global firmware geometry. `detect_fb_size()` changes them; reset to defaults and detect per card. |
| `g_Wpr2LoPreKill/g_Wpr2HiPreKill` | Global snapshot from before GFW reset; reset and recapture per card. |
| `cmp90_next_high_slot` | Global monotonic address cursor used by `alloc_fwsec_buffer()`. Payload pages use `AllocatePages` and remain reserved through application exit unless explicitly freed. |
| `cmp90_metaLowPhys` | Global cached below-4-GiB page. It is allocated once and reused; it must be tracked and not be mistaken for card-local memory. |
| `g_ryzenApu`, `gRbAll[]/gRbAllN/gBrIdx` | Host topology/probe state. These can be shared only while the host topology is unchanged; they are not GPU firmware state. |
| `pcie_gen_fails`, `cmp90_stockSig`, `cmp90_skipMapper`, `cmp90_corruptMeta` | File-scope diagnostics/legacy switches. Reset or prove immutable before reuse. |
| `g_bmBuf/g_bmSize/g_bmDp/g_bmPath`, `g_bootPathCnt` | OS-loader preload and chainload state, application-wide; load once, chainload once after all GPU operations. |
| Logger globals and function statics (`u40x_wide`, duplicate-line cache, `first`) | Application-wide log state; safe to share, but log each operation with BDF and status. |
| `u40x_vbios_dump()` static 1 MiB ROM buffer | Temporary application buffer, not per-GPU firmware state. |
| `efi_main()` payload locals (`v67Phys`, `ucodePhys`, `blPhys`, `radixPhys`, `fwPhys`, `wprMetaPhys`, optional `fwsecPhys`) | Addresses are local, but their EFI pages are reserved after return unless explicitly freed. Failure paths jump to `done`; they do not free these pages. |
| Function-local scratch buffers / GUIDs | Short-lived or immutable helper state; audit before moving helper calls across GPU iterations. |

No persistent BAR MMIO mapping object is used: accesses dereference `gBar0Base`
plus an offset. This still makes `gBar0Base` a critical per-card selector.

## Same-boot safety design

The worker retains its firmware allocations through Ubuntu handoff. This is
intentional: the source does not establish when an unlocked GPU ceases to
reference any particular payload. `--all` therefore never frees or reuses a
card's retained pages, never issues FLR, and stops on the first failed or
uncertain worker result. The current hardware evidence proves discovery-only
enumeration and Ubuntu chainloading; the staged unlock procedure below remains
required before treating eight-card operation as hardware-validated.

The memory lifetime is material. `alloc_fwsec_buffer()` and
`alloc_below_4g()` request `EfiReservedMemoryType` pages. The one-card path
allocates a 29.4 MB (28 MiB) dummy firmware image (`0x1bfadc8`) and a
separate radix allocation containing another copy of that image, then
allocates the V67, booter ucode, GSP boot code, WPR metadata, and possibly
FWSEC. There is no
`FreePages` cleanup. The first two allocations alone retain about 58.7 MB
(56 MiB) per card, or roughly 470 MB (448 MiB) for eight cards, through
chainload; failed attempts can retain additional partial allocations. The
code does not establish when the GPU has finished referencing every buffer,
so freeing or reusing them
while any card remains unlocked is not justified.

The reset routines operate on the currently selected card through the global
BAR0 base: the flow kills GSP firmware and resets/sanitizes SEC2. They are not
a demonstrated full-device recovery contract for a card whose unlock fails.
`do_flr()` is defined, and its own comment says FLR clears latched WPR2; it is
not called by the release flow. Adding FLR as inter-card cleanup would
therefore introduce an unverified state change. Do not reset an earlier
unlocked card to prepare the next one.

The coordinator retains the historical ReBAR behavior only for a successful
single-card/default invocation. `--all` explicitly skips it. The coordinator
preloads the OS loader once and chainloads only after the final worker returns.

## Implemented architecture

`efi_main()` validates mutually exclusive modes, discovers and snapshots the
target BDF list once, preloads once, coordinates results, and chainloads once.
`unlock_one_gpu(const CMP50_BDF *, CMP50_UNLOCK_CONTEXT *,
CMP50_UNLOCK_RESULT *)` binds a fresh context before each run, resets the
active BDF/BAR/WPR globals to defaults, detects that card's framebuffer/WPR
geometry, and returns without chainloading or ReBAR changes. The result stores
the EFI status, SS0, SS1, and an independent verification flag.

Each allocation records physical address, page count, EFI type, purpose,
owning context, and the conservative `gpuMayReference` lifetime flag. The
capacity preflight counts EFI conventional-memory pages against the retained
per-card working-set estimate. Each worker logs BDF, SS0, SS1, result, and
retained KiB. The coordinator emits an aggregate attempted/success/retained
summary and fail-stops after a worker failure.

Validation should progress from the known-working `02:00.0` through two
cards, then four, then all eight, using BootNext each time. Confirm both
readbacks for every card, all 18 GPUs in Ubuntu, unchanged Xid counts, and
successful Ubuntu chainload after each step. A failed card requires a cold
boot before another experiment unless a recovery sequence is independently
proven. Until those steps pass, the single-card and discovery-only modes are
the only supported write/diagnostic modes.

## Build

On Ubuntu 24.04 with the repository GNU-EFI prerequisites installed:

```bash
sudo apt install build-essential gnu-efi
cd efi-unlock
./build.sh
```

The expected artifact is `50HXUNLK.EFI`. Do not install it automatically. In
the current development environment, WSL has no `apt-get`, `/usr/include/efi`,
or usable GNU-EFI setup, so a successful build has not yet been demonstrated.

## Discovery-only check

Build the EFI file, copy it manually to a test ESP, create a temporary firmware
entry manually, and use `--discover-only` in its load options. First set
`BootNext` to that entry once; do not change `BootOrder`. The log should list
all eight expected BDFs and report discovery complete. Verify the P104 devices
are absent and the log says no PCI/GPU firmware writes were performed. The
application then chainloads the existing OS loader. Discovery-only is also
available with `--return-to-grub` when called from a GRUB menu entry.

## Single-card test and incremental hardware procedure

Start with only `02:00.0` selected (`--bdf=02:00.0`) and a one-time BootNext
entry. Confirm the log reports that BDF, SS0 `0x88888888`, SS1 `0x8`, and a
successful chainload; after boot, verify all 18 GPUs remain operational and
compare `nvidia-smi` and kernel Xid logs with the pre-test baseline. Then cold
boot and repeat one card at a time for `03:00.0`, `0D:00.0`, `10:00.0`,
`11:00.0`, `12:00.0`, `14:00.0`, and `15:00.0`.

A one-card-per-boot test proves selection and card compatibility; it does not
prove sequential multi-card operation because GPU state resets on reboot. Do
not attempt same-boot multi-card unlocking until the implementation adds
per-card state reset, allocation tracking/freeing, per-BDF error isolation,
and independent post-write verification, and passes discovery/build review.

## Rollback

BootNext is consumed once, so the normal BootOrder resumes on the next boot.
Remove the manually copied EFI file and temporary boot entry after testing.
The unlock is runtime state and disappears on GPU reset. No VBIOS or driver
changes are part of this source change.

## Source review and limits

Discovery scans all PCI buses/functions using read-only CF8/CFC config reads.
Explicit selection is checked against that discovered set before the existing
BAR enable and unlock path. Default selection retains the historic first-card
behavior. The code does not change NVIDIA driver files, P104 card config,
VBIOS, PCIe generation, ReBAR, or clocks.

The new discovery/parser path has not been run in UEFI hardware or QEMU, and
the successful GNU-EFI build remains outstanding. The actual eight-card
sequential unlock and per-operation error isolation are not implemented in
this revision; do not treat this branch as satisfying the multi-card unlock
requirement or deploy it for all-card unlocking.
