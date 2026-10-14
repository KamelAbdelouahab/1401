"""Linux scan helpers: the native ACPI dump and the sysfs answers used when lspci/lsusb are not installed."""
import os
import tempfile
import unittest

from p1401 import linuxscan


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(text, bytes) else "w") as fh:
        fh.write(text)


class AcpiDump(unittest.TestCase):
    def test_static_tables_only_named_like_acpidump(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "tables")
            write(os.path.join(src, "DSDT"), b"DSDT" + bytes(60))
            write(os.path.join(src, "SSDT1"), b"SSDT" + bytes(60))
            write(os.path.join(src, "FACP"), b"FACP" + bytes(260))
            write(os.path.join(src, "dynamic", "SSDT9"), b"SSDT" + bytes(60))
            write(os.path.join(src, "data", "BERT"), b"x" * 64)
            write(os.path.join(src, "TINY"), b"TINY")
            out = os.path.join(d, "ACPI")
            write(os.path.join(out, "stale.aml"), b"old")
            names = linuxscan.dump_acpi(out, tables=src)
            self.assertEqual(sorted(names), ["DSDT.aml", "FACP.aml", "SSDT1.aml"])
            self.assertEqual(sorted(os.listdir(out)), ["DSDT.aml", "FACP.aml", "SSDT1.aml"])
            with open(os.path.join(out, "DSDT.aml"), "rb") as fh:
                self.assertEqual(fh.read(4), b"DSDT")

    def test_missing_sysfs(self):
        with self.assertRaises(linuxscan.LinuxScanError):
            linuxscan.dump_acpi(tempfile.mkdtemp(), tables="/nonexistent/acpi")


class Lspci(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "pci")
        for slot, cls, ven, dev in [("0000:01:00.0", "0x030000", "0x10de", "0x2882"),
                                    ("0000:00:1f.6", "0x020000", "0x8086", "0x15bc"),
                                    ("0000:02:00.0", "0x010802", "0x144d", "0xa80a"),
                                    ("0000:03:00.0", "0x0d1100", "0x7777", "0x0001")]:
            for k, v in (("class", cls), ("vendor", ven), ("device", dev)):
                write(os.path.join(self.root, slot, k), v + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self, slot):
        out, rc = linuxscan.lspci_vmm(slot, root=self.root)
        self.assertEqual(rc, 0)
        return dict(line.split(":\t", 1) for line in out.splitlines())

    def test_gpu_network_storage_classes_match_lspci_wording(self):
        gpu = self.parse("0000:01:00.0")
        self.assertEqual(gpu["Class"], "VGA compatible controller")
        self.assertEqual(gpu["Vendor"], "NVIDIA Corporation")
        self.assertIn("GeForce RTX 4060", gpu["Device"])
        self.assertEqual(self.parse("0000:00:1f.6")["Class"], "Ethernet controller")
        self.assertEqual(self.parse("0000:02:00.0")["Class"], "Non-Volatile memory controller")

    def test_unknown_ids_still_answer(self):
        other = self.parse("0000:03:00.0")
        self.assertTrue(other["Vendor"].startswith("Device 7777"))
        self.assertEqual(linuxscan.lspci_vmm("0000:09:00.0", root=self.root), ("", 1))

    def test_the_sniffer_parser_reads_it(self):
        # the same loop Hardware Sniffer's get_pci_device_name_and_class runs over lspci output
        out, _ = linuxscan.lspci_vmm("0000:01:00.0", root=self.root)
        device_class = vendor = device = None
        for line in out.splitlines():
            low = line.lower()
            if "class" in low:
                device_class = line.split(":")[-1].strip()
            elif "vendor" in low:
                vendor = line.split(":")[-1].strip()
            elif "device" in low:
                device = line.split(":")[-1].strip()
            if device_class and device:
                break
        self.assertEqual(device_class, "VGA compatible controller")
        self.assertTrue(vendor and device)

    def test_wrap_run_only_replaces_missing_tools(self):
        calls = []
        run = linuxscan.wrap_run(lambda comm: calls.append(comm) or ("real", "", 0), which=lambda n: None)
        self.assertEqual(sorted(run.fallbacks), ["lspci", "lsusb"])
        self.assertEqual(run({"args": ["lspci", "-vmm", "-s", "0000:77:00.0"]})[2], 1)
        self.assertEqual(run({"args": ["ls"]}), ("real", "", 0))
        present = linuxscan.wrap_run(lambda comm: ("real", "", 0), which=lambda n: "/usr/bin/" + n)
        self.assertEqual(present({"args": ["lspci", "-vmm", "-s", "x"]}), ("real", "", 0))


class Lsusb(unittest.TestCase):
    def test_names_from_sysfs_and_usb_ids(self):
        with tempfile.TemporaryDirectory() as d:
            for k, v in (("idVendor", "8087"), ("idProduct", "0026"), ("bDeviceProtocol", "01"), ("product", "AX201 BT")):
                write(os.path.join(d, "1-14", k), v + "\n")
            out, rc = linuxscan.lsusb_v("8087", "0026", root=d)
            self.assertEqual(rc, 0)
            self.assertIn("idVendor           0x8087 Intel Corp.", out)
            self.assertIn("iProduct                2 AX201 BT", out)
            self.assertEqual(linuxscan.lsusb_v("0000", "0000", root=d)[1], 1)


class LinuxReportPaths(unittest.TestCase):
    def test_paths_outside_the_system_bus_are_dropped_not_the_report(self):
        from p1401 import report
        r = {"GPU": {}, "System Devices": {
            "LNXTHERM": {"Bus Type": "ACPI", "Device": "LNXTHERM", "ACPI Path": "\\_TZ_.TZ1_"},
            "PNP0103": {"Bus Type": "ACPI", "Device": "PNP0103", "ACPI Path": "\\HPET"},
            "PNP0C09": {"Bus Type": "ACPI", "Device": "PNP0C09", "ACPI Path": "\\_SB.PCI0.SBRG.EC0_"}}}
        out, notes = report.normalize(r)
        dev = out["System Devices"]
        self.assertNotIn("ACPI Path", dev["LNXTHERM"])
        self.assertNotIn("ACPI Path", dev["PNP0103"])
        self.assertEqual(dev["PNP0C09"]["ACPI Path"], "\\_SB.PCI0.SBRG.EC0_")
        self.assertEqual(sum("outside the system bus" in n for n in notes), 2)


if __name__ == "__main__":
    unittest.main()
