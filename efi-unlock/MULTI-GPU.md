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

This is a discovery and explicit single-card selection change. Sequential
multi-card unlock is intentionally not enabled yet: the current source does
not provide a proven per-card cleanup boundary, and reusing the large unlock
routine in a loop could leave a failed GPU or retained EFI allocation
contaminating the next card. Do not pass a multi-card option; none exists.

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
