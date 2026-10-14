"""What the Linux scan needs beyond Hardware Sniffer's own Linux code, without editing it (upstream stays pinned).

- ACPI: the Sniffer copies /sys/firmware/acpi/tables with `sudo cp` and `sudo chown`, which fails where sudo is
  missing or asks for a password in a window that has no terminal. 1401's scan already runs as root (the window asks
  through polkit), so dump_acpi() reads the firmware's tables directly: every static table, named <signature>.aml, the
  way acpidump names them on Windows. Tables the kernel loaded later at run time (dynamic/) are not firmware tables.
- lspci and lsusb: the Sniffer asks them for device names and the PCI class that decides what is a GPU, a network or
  a storage controller. Minimal installs ship neither (pciutils, usbutils). When one is missing, run() answers the same
  two questions from sysfs and the pci.ids/usb.ids the Sniffer bundles, in the same output format the Sniffer parses.
"""
import os
import shutil

ACPI_TABLES = "/sys/firmware/acpi/tables"
PCI = "/sys/bus/pci/devices"
USB = "/sys/bus/usb/devices"
HERE = os.path.dirname(os.path.abspath(__file__))
DATASETS = os.path.join(os.path.dirname(HERE), "upstream", "Hardware-Sniffer", "Scripts", "datasets")


class LinuxScanError(RuntimeError):
    pass


def dump_acpi(acpi_dir, tables=ACPI_TABLES):
    """Copies the firmware's ACPI tables into acpi_dir. Returns the file names written."""
    if not os.path.isdir(tables):
        raise LinuxScanError(f"{tables} is missing: this PC was not started in a way that shows its ACPI tables "
                             "(the kernel needs CONFIG_ACPI and sysfs)")
    os.makedirs(acpi_dir, exist_ok=True)
    for old in os.listdir(acpi_dir):
        p = os.path.join(acpi_dir, old)
        if os.path.isfile(p):
            os.remove(p)
    written, denied = [], []
    for name in sorted(os.listdir(tables)):
        src = os.path.join(tables, name)
        if not os.path.isfile(src):
            continue  # data/ and dynamic/: not tables the firmware hands to an OS
        try:
            with open(src, "rb") as fh:
                data = fh.read()
        except PermissionError:
            denied.append(name)
            continue
        if len(data) < 36:
            continue
        dst = os.path.join(acpi_dir, name.upper() + ".aml")
        with open(dst, "wb") as fh:
            fh.write(data)
        written.append(os.path.basename(dst))
    if denied and not written:
        raise LinuxScanError("the ACPI tables can only be read as administrator (root); start the check from the 1401 "
                             "window, which asks for your password")
    return written


def _ids(path):
    """pci.ids / usb.ids -> ({vendor: (name, {device: name})}, {class: (name, {subclass: name})})."""
    vendors, classes = {}, {}
    vendor = cls = None
    section = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                if line.startswith("C "):
                    code, _, name = line[2:].partition("  ")
                    cls = code.strip().lower()
                    classes[cls] = (name.strip(), {})
                    section = "class"
                    continue
                if not line.startswith("\t"):
                    head = line.split(None, 1)
                    if len(head) == 2 and len(head[0]) == 4 and all(c in "0123456789abcdefABCDEF" for c in head[0]):
                        vendor = head[0].lower()
                        vendors[vendor] = (head[1].strip(), {})
                        section = "vendor"
                    else:
                        section = None  # other top-level lists in usb.ids (languages, HID usages...)
                    continue
                if line.startswith("\t\t"):
                    continue  # subsystems / programming interfaces
                key, _, name = line[1:].partition("  ")
                if section == "vendor" and vendor:
                    vendors[vendor][1][key.strip().lower()] = name.strip()
                elif section == "class" and cls:
                    classes[cls][1][key.strip().lower()] = name.strip()
    except OSError:
        pass
    return vendors, classes


_PCI = _USB = None


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def lspci_vmm(slot, root=PCI, ids=None):
    """`lspci -vmm -s <slot>` for one device, from sysfs."""
    global _PCI
    if ids is None:
        if _PCI is None:
            _PCI = _ids(os.path.join(DATASETS, "pci.ids"))
        ids = _PCI
    vendors, classes = ids
    d = os.path.join(root, slot)
    if not os.path.isdir(d):
        return "", 1
    vid = _read(os.path.join(d, "vendor")).lower().replace("0x", "")
    did = _read(os.path.join(d, "device")).lower().replace("0x", "")
    code = _read(os.path.join(d, "class")).lower().replace("0x", "").zfill(6)
    cname, subs = classes.get(code[:2], ("Class " + code[:4], {}))
    cls = subs.get(code[2:4], cname)
    vname, devices = vendors.get(vid, (f"Device {vid}", {}))
    dname = devices.get(did, f"Device {did}")
    lines = [f"Slot:\t{slot}", f"Class:\t{cls}", f"Vendor:\t{vname}", f"Device:\t{dname}"]
    return "\n".join(lines) + "\n", 0


def lsusb_v(vid, pid, root=USB, ids=None):
    """The lines of `lsusb -v -d vid:pid` the Sniffer reads, from sysfs and usb.ids."""
    global _USB
    if ids is None:
        if _USB is None:
            _USB = _ids(os.path.join(DATASETS, "usb.ids"))
        ids = _USB
    vendors, _ = ids
    vid, pid = vid.lower(), pid.lower()
    found = None
    if os.path.isdir(root):
        for dev in sorted(os.listdir(root)):
            d = os.path.join(root, dev)
            if _read(os.path.join(d, "idVendor")).lower() == vid and _read(os.path.join(d, "idProduct")).lower() == pid:
                found = d
                break
    vname, products = vendors.get(vid, ("", {}))
    pname = products.get(pid, "")
    if not found and not (vname or pname):
        return "", 1
    proto = _read(os.path.join(found, "bDeviceProtocol")) if found else ""
    lines = ["Device Descriptor:"]
    if proto:
        lines.append(f"  bDeviceProtocol {int(proto, 16):>8}")
    lines.append(f"  idVendor           0x{vid} {vname}".rstrip())
    lines.append(f"  idProduct          0x{pid} {pname}".rstrip())
    product = _read(os.path.join(found, "product")) if found else ""
    if product:
        lines.append(f"  iProduct                2 {product}")
    return "\n".join(lines) + "\n", 0


def wrap_run(run, which=shutil.which):
    """The Sniffer's run({"args": [...]}) with sysfs answers for lspci/lsusb when those tools are not installed."""
    have = {"lspci": bool(which("lspci")), "lsusb": bool(which("lsusb"))}

    def patched(comm):
        args = list(comm.get("args") or [])
        if args[:3] == ["lspci", "-vmm", "-s"] and len(args) == 4 and not have["lspci"]:
            out, rc = lspci_vmm(args[3])
            return out, "", rc
        if args[:3] == ["lsusb", "-v", "-d"] and len(args) == 4 and not have["lsusb"]:
            vid, _, pid = args[3].partition(":")
            out, rc = lsusb_v(vid, pid)
            return out, "", rc
        return run(comm)
    patched.fallbacks = [k for k, v in have.items() if not v]
    return patched


def is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0

