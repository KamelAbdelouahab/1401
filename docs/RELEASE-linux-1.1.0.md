# 1401 1.1.0 for Linux

The 1401 app for Linux, tested on **Debian 13 (trixie)** and **CachyOS** (x86_64). Same steps as the Windows app -
Check this PC, Build the Mac setup, Your BIOS steps, Create the macOS stick - on the same engine and the same pinned
OpenCore-Simplify / Hardware Sniffer, with the same safety rules for the stick. How it differs and why: `docs/LINUX.md`.

## Install

```bash
tar xzf 1401-Linux-1.1.0-x86_64.tar.gz
./1401/1401              # or double-click it
./1401/install.sh        # optional: adds 1401 to your application menu
```

Nothing to install first on a KDE or GNOME desktop: Python 3.12, Qt 6, `iasl` and certificates come with it (on X11
without `libxkbcommon-x11`, 1401 names the package to install). The scan and the stick write ask for
your password (polkit, or sudo when `pkexec` is missing). The NVIDIA driver package and the 1401 Mac app (Mac 1.1.0)
are included in `NullMoth/`, as in the Windows package.

## What is new compared with the Windows code

- Stick writer for Linux: lsblk discovery, USB only, never the running system's disk (also through LUKS/LVM/RAID),
  re-identified before erasing, unmount + exclusive open, one active FAT32 partition (<= 16 GiB) on an MBR, mkfs.fat or
  a built-in FAT32 formatter when dosfstools is missing, flushed and unmounted when done.
- ACPI tables read directly from sysfs; device names from sysfs when pciutils/usbutils are missing.
- BIOS steps written for Linux: LUKS+TPM and BitLocker keys, the AHCI driver in the initramfs before leaving
  RAID/VMD (Debian, Arch/CachyOS, dracut), moving GRUB/systemd-boot to UEFI, and **Restart into BIOS setup**.
- Engine fix: Linux scans with ACPI devices outside `\_SB` (thermal zones, HPET) were rejected by the report schema.

## Verified

On Debian 13 and CachyOS (containers with each distribution's own packages, GitHub Actions): 334 unit tests with the
packaged Python including a real loop-device erase, the packaged window's `--verify-package` check, and full EFI builds
of 11 public hardware reports, every config passing OpenCore's `ocvalidate`. The complete stick write ran in a VM with
an emulated USB stick (both FAT32 formatters), checked by `fsck.fat`. Not verified here: a physical PC's firmware, a physical stick, and
Apple's recovery download (unchanged engine code).

This is a modified build of 1401 under PolyForm Noncommercial 1.0.0; it is not made or endorsed by NullMoth Systems.
