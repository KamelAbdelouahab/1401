# 1401 for Linux

The Linux app does what the Windows app does, step for step: **Check this PC**, **Build the Mac setup**, **Your BIOS
steps**, **Create the macOS stick**, and the same links (scan for logs and send them, update the NVIDIA driver, check
your own EFI, firmware prerequisites). It runs the same engine (`p1401/`) on the same pinned upstream code; only the
parts that talk to the operating system differ. Tested on **Debian 13 (trixie)** and **CachyOS** (x86_64).

> 1401 is new and may not work on every PC. If it does not work on yours, set up OpenCore by hand with the
> [OpenCore Install Guide](https://dortania.github.io/OpenCore-Install-Guide/).

## Using it

1. Download `1401-Linux-1.1.0-x86_64.tar.gz` and unpack it anywhere in your home folder:
   `tar xzf 1401-Linux-1.1.0-x86_64.tar.gz`
2. Run `1401/1401` (double-click it, or `./1401` in a terminal). Optional: `1401/install.sh` adds it to your
   application menu (for your user only; `./install.sh --uninstall` removes the entry).
3. **Check this PC** asks for your password once (polkit, or sudo if polkit's `pkexec` is not installed): the
   firmware's ACPI tables are readable only by root. **Create the macOS stick** uses the same permission to erase the
   stick. Everything else runs as you.
4. The rest is the same as on Windows (README.md, "Using it").

Nothing has to be installed first. The package brings its own Python 3.12, Qt 6, `iasl` and certificates. It uses the
desktop's standard libraries (OpenGL/EGL, fontconfig, freetype, xkbcommon, Wayland or X11), which every KDE, GNOME,
Xfce or other desktop install has. Optional: `pciutils`/`usbutils` (otherwise 1401 reads device names from sysfs and
its own ID lists) and `dosfstools` (otherwise 1401 formats the stick with its own FAT32 formatter).

## What is different from Windows, and why

| Windows (1401.exe) | Linux (`./1401`) |
|---|---|
| WinForms window, .NET Framework 4.8 | Qt 6 window (PySide6) in `app/a1401/`, one module per Windows source file |
| Embedded Python 3.12 + pywin32, WMI | python-build-standalone 3.12 + PySide6 + certifi, pruned |
| Administrator for the whole app (UAC) | The window runs as you; `app/a1401/helper.py` runs as root through polkit/sudo and accepts only `p1401.scan` and `p1401.usbwriter write` |
| WMI hardware scan, `acpidump.exe` | Hardware Sniffer's Linux scanner (sysfs); ACPI tables read from `/sys/firmware/acpi/tables` (`p1401/linuxscan.py`); `lspci`/`lsusb` answered from sysfs when not installed |
| `Get-Disk`, volume lock, `IOCTL_DISK_SET_DRIVE_LAYOUT_EX`, `Format-Volume` | `lsblk`, unmount + `O_EXCL` open, MBR written directly, `BLKRRPART`, `mkfs.fat` or the built-in formatter (`p1401/linuxdisk.py`, `p1401/fat32.py`) |
| Drive letter stays assigned | Mounted privately while writing, then flushed and unmounted: safe to unplug |
| `%LOCALAPPDATA%\NullMoth\1401` | `~/.local/share/1401` (scans, builds, reports), `~/.cache/1401/OCK_Files` (downloads) |
| WebBrowser control for the BIOS steps | Qt's HTML view, plus "Open this page in your web browser" |
| BIOS steps written for Windows (BitLocker, `bcdedit`, MBR2GPT) | Written for Linux (`guide.py --host linux`): LUKS+TPM and Windows' BitLocker keys, the AHCI driver in the initramfs before leaving RAID/VMD, moving GRUB/systemd-boot to UEFI; **Restart into BIOS setup** uses `systemctl reboot --firmware-setup` |

The stick is identical: MBR, one active FAT32 partition of at most 16 GiB labelled `1401`, `EFI/`,
`com.apple.recovery.boot/` from Apple (every chunk checked against Apple's signed chunklist), `NullMoth/`.

The safety rules are the same as on Windows and live in the engine, not the window: USB only; never a disk that holds
the running system (`/`, `/boot`, `/boot/efi`, `/usr`, `/home`, `/var`, swap - also through LUKS, LVM or RAID); never a
read-only disk, a disk under 2 GiB, or one with an installed OS (EFI system, APFS, HFS+, Microsoft reserved, Windows
recovery partition); over 256 GiB only after confirming; the disk is identified again (model, serial, size, bus) right
before erasing.

One engine fix came out of the port: Hardware Sniffer on Linux reports ACPI devices outside the system bus (thermal
zones `\_TZ_`, `\HPET`), and the engine's report schema rejected the whole report. `p1401/report.py` now drops just those
optional paths and notes it in the build log.

## How it was tested

- The full unit-test suite with the packaged Python inside Debian 13 and CachyOS images, including a real erase and
  FAT32 format of a loop device (`P1401_LOOP_TESTS=1 python -m unittest tests.test_linuxdisk`), and planning over the
  15-machine public corpus.
- The packaged window's own check (`./1401 --verify-package result.json`, the Linux port of `--verify-package` on
  Windows) on Debian 13 and CachyOS under a virtual display.
- The complete stick write (`p1401.usbwriter write`) in a VM with an emulated USB mass-storage stick: discovery over
  USB, refusing the system disk, erase, partition table, both FAT32 formatters, the kernel's vfat mount, copy,
  read-back verification, unmount; the result checked with `fsck.fat`.
- `.github/workflows/linux-candidate.yml` builds the package and runs full EFI builds (downloads + `ocvalidate`) on
  Debian 13 and CachyOS.

Not tested here: a real PC's firmware and a physical stick end to end, and Apple's recovery download from Linux (the
same `p1401/apple.py` the Windows app uses). Run **Check this PC** and send the logs if something fails.

## Building the package

```bash
python3 tools/linux_release.py prepare --stage /tmp/stage --cache /tmp/cache   # needs readelf, zstd, git
xvfb-run python3 tools/linux_release.py check --stage /tmp/stage --output /tmp/evidence.json
python3 tools/linux_release.py package --stage /tmp/stage --evidence /tmp/evidence.json --output dist
```
