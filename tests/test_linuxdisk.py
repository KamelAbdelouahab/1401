"""Linux stick writer: disk discovery and safety rules from lsblk trees, the MBR it writes, and (as root, with a loop
device) the real erase + FAT32 format checked with dosfstools' fsck.fat."""
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from p1401 import fat32, linuxdisk, usbwriter


def stick(**over):
    node = {"name": "sdb", "path": "/dev/sdb", "type": "disk", "size": 16008609792, "tran": "usb", "rm": True,
            "hotplug": True, "ro": False, "model": "Ultra", "vendor": "SanDisk", "serial": "4C530001", "pttype": "dos",
            "mountpoints": [None],
            "children": [{"name": "sdb1", "path": "/dev/sdb1", "type": "part", "parttype": "0xc", "fstype": "vfat",
                          "label": "STICK", "mountpoints": ["/run/media/me/STICK"]}]}
    node.update(over)
    return node


SYSTEM = {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "size": 512110190592, "tran": "nvme", "rm": False,
          "hotplug": False, "ro": False, "model": "Samsung SSD 980", "serial": "S1", "pttype": "gpt", "mountpoints": [None],
          "children": [
              {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "type": "part", "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
               "fstype": "vfat", "mountpoints": ["/boot/efi"]},
              {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "type": "part", "parttype": "0fc63daf-8483-4772-8e79-3d69d8477de4",
               "fstype": "crypto_LUKS", "mountpoints": [None],
               "children": [{"name": "luks-1", "type": "crypt", "mountpoints": [None],
                             "children": [{"name": "vg-root", "type": "lvm", "mountpoints": ["/"]}]}]}]}


class Describe(unittest.TestCase):
    def test_usb_stick(self):
        d = linuxdisk.describe(stick())
        self.assertEqual((d["Number"], d["Path"], d["BusType"], d["FriendlyName"]), ("sdb", "/dev/sdb", "USB", "SanDisk Ultra"))
        self.assertFalse(d["IsBoot"] or d["IsSystem"] or d["IsReadOnly"])
        self.assertEqual(d["Mountpoints"], ["/run/media/me/STICK"])
        ok, why = usbwriter.eligible(d)
        self.assertTrue(ok, why)

    def test_root_through_luks_and_lvm_is_the_system_disk(self):
        d = linuxdisk.describe(SYSTEM)
        self.assertTrue(d["IsBoot"] and d["IsSystem"])
        self.assertIn("/", d["SystemMounts"])
        ok, why = usbwriter.eligible(d)
        self.assertFalse(ok)
        self.assertTrue(any("not a USB" in w for w in why) and any("booted" in w for w in why))

    def test_usb_disk_running_linux_is_refused(self):
        d = linuxdisk.describe(stick(children=[{"name": "sdb1", "path": "/dev/sdb1", "type": "part", "parttype": "0x83",
                                                "mountpoints": ["/"]}]))
        ok, why = usbwriter.eligible(d)
        self.assertFalse(ok)
        self.assertTrue(any("booted" in w for w in why))

    def test_swap_on_the_stick_counts_as_system(self):
        d = linuxdisk.describe(stick(children=[{"name": "sdb1", "type": "part", "mountpoints": ["[SWAP]"]}]))
        self.assertTrue(d["IsSystem"])

    def test_internal_sata_and_read_only(self):
        self.assertEqual(linuxdisk.describe(stick(tran="sata"))["BusType"], "SATA")
        ok, why = usbwriter.eligible(linuxdisk.describe(stick(ro=True)))
        self.assertFalse(ok)
        self.assertTrue(any("read-only" in w for w in why))

    def test_old_util_linux_single_mountpoint(self):
        node = stick()
        node["children"][0].pop("mountpoints")
        node["children"][0]["mountpoint"] = "/media/old"
        node.pop("mountpoints")
        self.assertEqual(linuxdisk.describe(node)["Mountpoints"], ["/media/old"])

    def test_loop_devices_only_with_the_test_switch(self):
        loop = {"name": "loop0", "path": "/dev/loop0", "type": "loop", "size": 3 << 30, "tran": None, "mountpoints": [None]}
        with patch.object(linuxdisk, "lsblk", return_value=[loop, stick()]):
            with patch.object(linuxdisk, "TEST_LOOP", False):
                self.assertEqual([d["Number"] for d in linuxdisk.list_disks()], ["sdb"])
            with patch.object(linuxdisk, "TEST_LOOP", True):
                self.assertEqual([d["BusType"] for d in linuxdisk.list_disks()], ["USB", "USB"])


class Recheck(unittest.TestCase):
    def run_recheck(self, picked, now_node):
        with patch.object(linuxdisk, "lsblk", return_value=[now_node]):
            return linuxdisk.recheck(picked)

    def test_same_stick_passes(self):
        picked = linuxdisk.describe(stick())
        self.assertEqual(self.run_recheck(picked, stick())["Path"], "/dev/sdb")

    def test_swapped_stick_is_refused_before_erasing(self):
        picked = linuxdisk.describe(stick())
        for change in ({"serial": "OTHER"}, {"size": 32 << 30}, {"model": "Cruzer"}, {"tran": "sata"}):
            with self.assertRaisesRegex(linuxdisk.DiskError, "Nothing was erased"):
                self.run_recheck(picked, stick(**change))

    def test_unplugged(self):
        with patch.object(linuxdisk, "lsblk", return_value=[]):
            with self.assertRaisesRegex(linuxdisk.DiskError, "not attached"):
                linuxdisk.recheck(linuxdisk.describe(stick()))

    def test_os_partitions_are_refused(self):
        for guid in linuxdisk.OS_TYPES:
            node = stick(pttype="gpt", children=[{"name": "sdb1", "path": "/dev/sdb1", "type": "part", "parttype": guid,
                                                  "mountpoints": [None]}])
            with self.assertRaisesRegex(linuxdisk.DiskError, "operating system"):
                self.run_recheck(linuxdisk.describe(node), node)

    def test_open_luks_on_the_stick_is_refused(self):
        node = stick(children=[{"name": "sdb1", "path": "/dev/sdb1", "type": "part", "parttype": "0x83", "mountpoints": [None],
                                "children": [{"name": "luks-x", "type": "crypt", "mountpoints": [None]}]}])
        with self.assertRaisesRegex(linuxdisk.DiskError, "encrypted volume"):
            self.run_recheck(linuxdisk.describe(node), node)


class Layout(unittest.TestCase):
    def test_mbr(self):
        table, start, length = linuxdisk.mbr(16008609792, 16 << 30, 512, signature=0x1401)
        self.assertEqual(len(table), 512)
        self.assertEqual(table[510:], b"\x55\xAA")
        status, ptype, lba, count = table[446], table[450], *struct.unpack_from("<II", table, 454)
        self.assertEqual((status, ptype, lba), (0x80, 0x0C, 2048))
        self.assertEqual(start, 2048)
        self.assertLessEqual((lba + count) * 512, 16008609792 - linuxdisk.MiB)
        self.assertEqual(struct.unpack_from("<I", table, 440)[0], 0x1401)
        self.assertEqual(table[462:510], bytes(48))

    def test_large_stick_capped_at_16_gib(self):
        _, _, length = linuxdisk.mbr(64 * 10**9, usbwriter.PART_CAP, 512)
        self.assertEqual(length * 512, usbwriter.PART_CAP)

    def test_partition_names(self):
        self.assertEqual(linuxdisk.partition_path("/dev/sdb"), "/dev/sdb1")
        self.assertEqual(linuxdisk.partition_path("/dev/nvme1n1"), "/dev/nvme1n1p1")
        self.assertEqual(linuxdisk.partition_path("/dev/loop3"), "/dev/loop3p1")
        self.assertEqual(linuxdisk.partition_path("/dev/mmcblk0"), "/dev/mmcblk0p1")


class Fat32(unittest.TestCase):
    def test_layout_rules(self):
        for gib in (2, 4, 8, 15, 16):
            spc, fat, clusters = fat32.layout(gib * (1 << 30) // 512)
            self.assertGreaterEqual(clusters, fat32.MIN_CLUSTERS)
            self.assertGreaterEqual(fat * 128, clusters + 2)
        with self.assertRaises(fat32.FatError):
            fat32.layout(20 * (1 << 20) // 512)

    @unittest.skipUnless(shutil.which("fsck.fat"), "dosfstools not installed")
    def test_fsck_accepts_the_volume(self):
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "v.img")
            with open(img, "wb") as fh:
                fh.truncate(2 * (1 << 30) + 12345 * 512)
            fat32.format_volume(img, label="1401", hidden=2048)
            p = subprocess.run(["fsck.fat", "-n", img], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            with open(img, "rb") as fh:
                bs = fh.read(512)
            self.assertEqual(bs[82:90], b"FAT32   ")
            self.assertEqual(bs[71:82], b"1401       ")
            self.assertEqual(struct.unpack_from("<I", bs, 28)[0], 2048)


class CliLinux(unittest.TestCase):
    def test_cli_picks_the_disk_by_name_and_refuses_the_system_disk(self):
        with patch.object(usbwriter.platform, "system", return_value="Linux"), \
                patch.object(linuxdisk, "lsblk", return_value=[SYSTEM, stick()]):
            with patch("builtins.print") as out:
                self.assertEqual(usbwriter.cli_write(["nvme0n1", "/nonexistent", "24"]), 1)
            self.assertIn("STOP", out.call_args[0][0])
            with patch("builtins.print") as out:
                self.assertEqual(usbwriter.cli_write(["sdc", "/nonexistent", "24"]), 1)
            self.assertIn("not attached", out.call_args[0][0])


@unittest.skipUnless(os.geteuid() == 0 and os.path.exists("/dev/loop-control") and shutil.which("losetup")
                     and os.environ.get("P1401_LOOP_TESTS") == "1", "root + loop devices + P1401_LOOP_TESTS=1")
class LoopErase(unittest.TestCase):
    """The real erase on a file-backed loop device: O_EXCL lock, MBR, partition re-read, FAT32 (both formatters)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.img = os.path.join(self.dir, "stick.img")
        with open(self.img, "wb") as fh:
            fh.truncate(3 * (1 << 30))
            fh.seek(0)
            fh.write(b"OLDDATA" * 1000)
        self.loop = subprocess.check_output(["losetup", "-f", "--show", "-P", self.img], text=True).strip()

    def tearDown(self):
        subprocess.run(["losetup", "-d", self.loop])
        shutil.rmtree(self.dir)

    def erase(self, force_builtin):
        with patch.object(linuxdisk, "TEST_LOOP", True):
            picked = linuxdisk.find(self.loop)
            self.assertIsNotNone(picked)
            now = linuxdisk.recheck(picked)
            part, start, length, sector = linuxdisk.erase(now, usbwriter.PART_CAP, say=lambda m: None)
            real = shutil.which
            with patch.object(linuxdisk.shutil, "which", side_effect=lambda n: None if force_builtin else real(n)):
                linuxdisk.mkfs(part, "1401", start, sector, say=lambda m: None)
        self.assertEqual(part, self.loop + "p1")
        if shutil.which("fsck.fat"):
            p = subprocess.run(["fsck.fat", "-n", part], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout)
        with open(part, "rb") as fh:
            bs = fh.read(512)
        self.assertEqual((bs[82:90], bs[510:512]), (b"FAT32   ", b"\x55\xAA"))
        with open(self.img, "rb") as fh:
            table = fh.read(512)
        self.assertEqual((table[446], table[450]), (0x80, 0x0C))
        out = subprocess.check_output(["blkid", "-p", "-o", "export", part], text=True)
        self.assertIn("TYPE=vfat", out)
        self.assertIn("LABEL=1401", out)

    def test_erase_with_builtin_formatter(self):
        self.erase(True)

    @unittest.skipUnless(shutil.which("mkfs.fat"), "dosfstools not installed")
    def test_erase_with_mkfs_fat(self):
        self.erase(False)


if __name__ == "__main__":
    unittest.main()
