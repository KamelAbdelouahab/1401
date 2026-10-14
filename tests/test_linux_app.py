"""1401 for Linux, the parts that run without a display: the root helper's protocol and allow-list, local report
redaction and lifecycle, and the scan-evidence binding (the window itself is checked by its --verify-package mode)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "linux", "app")
sys.path.insert(0, APP)

from a1401 import durable, helper, scanevidence  # noqa: E402


class Redaction(unittest.TestCase):
    def test_home_user_and_host_are_removed(self):
        text = "/home/kamel/.local/share/1401/efi\nuser: kamel\nhostname=kamel-desktop\nopened by kamel on kamel-desktop"
        out = durable.clean_identity(text, "kamel", "kamel-desktop", "/home/kamel")
        self.assertNotIn("kamel", out)
        self.assertIn("~/.local/share/1401/efi", out)
        self.assertIn("this-pc", out)

    def test_vendor_named_account_keeps_hardware_names(self):
        out = durable.clean_identity("Manufacturer: Intel\nlogin intel\nIntel(R) Core i5", "intel", "box", "/home/intel")
        self.assertIn("Manufacturer: Intel", out)
        self.assertIn("Intel(R) Core", out)
        self.assertIn("login user", out)

    def test_save_find_and_refuse_foreign_folder(self):
        with tempfile.TemporaryDirectory() as work:
            first = durable.save(work, "build-final", "1.1.0", "output")
            second = durable.save(work, "build-final", "1.1.0", "again")
            self.assertTrue(first.saved and second.saved and first.path != second.path)
            self.assertEqual(set(durable.find(work)), {first.path, second.path})
            self.assertTrue(durable.is_local(work, first.path))
            foreign = os.path.join(work, "file")
            open(foreign, "w").close()
            self.assertFalse(durable.save(foreign, "x", "1", "y").saved)
            self.assertFalse(durable.save(work, "Bad Kind", "1", "y").saved)


class Evidence(unittest.TestCase):
    def test_binding(self):
        self.assertTrue(scanevidence.verify())


class Helper(unittest.TestCase):
    def test_allow_list(self):
        self.assertTrue(helper.allowed(["p1401.scan", "/x", "--run-id", "0" * 32]))
        self.assertTrue(helper.allowed(["p1401.usbwriter", "write", "sdb", "/efi", "24"]))
        for bad in (["p1401.usbwriter"], ["p1401.guide", "x"], ["p1401", "build"], ["os"], [], "p1401.scan",
                    ["p1401.scan", "a\0b"]):
            self.assertFalse(helper.allowed(bad), bad)

    def test_protocol(self):
        with tempfile.TemporaryDirectory() as work:
            p = subprocess.Popen([sys.executable, "-I", os.path.join(APP, "a1401", "helper.py"), "--app", REPO, "--work", work,
                                  "--uid", "4242", "--gid", "4242"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                 env={k: v for k, v in os.environ.items() if k not in ("PKEXEC_UID", "SUDO_UID", "SUDO_GID")})
            ready = json.loads(p.stdout.readline())
            self.assertTrue(ready["ready"])
            p.stdin.write("not json, e.g. a password sudo did not need\n")
            p.stdin.write(json.dumps({"argv": ["p1401.guide", "--selftest"]}) + "\n")
            p.stdin.flush()
            refused = [json.loads(p.stdout.readline()) for _ in range(2)]
            self.assertIn("does not run", refused[0]["line"])
            self.assertEqual(refused[1], {"exit": 1})
            # an allowed command really runs, and its output streams back line by line
            p.stdin.write(json.dumps({"argv": ["p1401.scan", "--selftest"], "env": {"LD_PRELOAD": "/evil.so"}}) + "\n")
            p.stdin.flush()
            lines = []
            while True:
                msg = json.loads(p.stdout.readline())
                if "exit" in msg:
                    break
                lines.append(msg["line"])
            self.assertTrue(any("ok" in l or "FAIL" in l or "corpus" in l for l in lines), lines[-5:])
            p.stdin.close()
            self.assertEqual(p.wait(10), 0)

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "needs root")
    def test_root_files_are_given_back(self):
        with tempfile.TemporaryDirectory() as work:
            d = os.path.join(work, "scan-x")
            os.makedirs(d)
            f = os.path.join(d, "Report.json")
            open(f, "w").close()
            os.symlink("/etc/passwd", os.path.join(d, "link"))
            before = os.stat("/etc/passwd").st_uid
            helper.give_back(work, 4242, 4242)
            self.assertEqual(os.stat(f).st_uid, 4242)
            self.assertEqual(os.stat("/etc/passwd").st_uid, before)


if __name__ == "__main__":
    unittest.main()
