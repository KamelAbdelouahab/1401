"""Runs the real Linux scan (Hardware Sniffer + 1401's ACPI dump and lspci/lsusb fallbacks) on a machine whose
firmware exposes no DMI/SMBIOS (cloud VMs, containers), by answering the /sys/class/dmi/id reads with fixed values.

    python3 tests/linux_scan_fake_dmi.py <out_dir>        (as root; writes Report.json + ACPI/)
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "upstream", "Hardware-Sniffer"))

from Scripts import utils  # noqa: E402

DMI = {"sys_vendor": "Gigabyte Technology Co., Ltd.", "product_name": "B550 AORUS ELITE", "board_vendor": "Gigabyte Technology Co., Ltd.",
       "board_name": "B550 AORUS ELITE", "chassis_type": "3", "bios_version": "F15", "bios_date": "01/01/2024"}
original = utils.Utils.read_file


def read_file(self, path):
    if isinstance(path, str) and path.startswith("/sys/class/dmi/id/") and not os.path.exists(path):
        return DMI.get(os.path.basename(path))
    return original(self, path)


utils.Utils.read_file = read_file

from p1401 import scan, scan_evidence  # noqa: E402

if __name__ == "__main__":
    out = sys.argv[1]
    token = scan_evidence.begin(out, None)
    rc = scan._sniffer_body(out, token)
    print("FAILURES:", scan.check(out))
    sys.exit(rc)
