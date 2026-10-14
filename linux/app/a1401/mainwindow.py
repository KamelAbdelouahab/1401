"""The 1401 window for Linux, step for step the Windows MainForm: Introduction, Check this PC, Build the Mac setup,
Your BIOS steps, Create the macOS stick, Done - with the same links (stick logs, NVIDIA driver update, check your own
EFI, firmware prerequisites) and the same consent before any log leaves the PC."""
import base64
import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTextBrowser, QTreeWidget,
                               QTreeWidgetItem, QWidget, QInputDialog)

from . import durable, engine, scanevidence, theme
from .privileged import Helper

if engine.APP_DIR not in sys.path:
    sys.path.insert(0, engine.APP_DIR)

STEPS = ["Introduction", "Check this PC", "Build the Mac setup", "Your BIOS steps", "Create the macOS stick", "Done"]
MIN_STICK = 2 * 1024 * 1024 * 1024
HERE = os.path.dirname(os.path.abspath(__file__))
# Unmodified NullMoth builds carry its name and mark; a modified build must not (NOTICE.md). The packager decides.
BRANDED = os.path.isfile(os.path.join(engine.HOME, "BRANDING-NULLMOTH"))
UPLOAD = "https://nullmothsystems.com/api/upload"


class UsbDisk:
    def __init__(self, d):
        self.raw = d
        self.number = d["Number"]
        self.name = d.get("FriendlyName") or d["Number"]
        self.size = int(d.get("Size") or 0)
        self.mounts = [m for m in d.get("Mountpoints") or [] if m and m != "[SWAP]"]

    def __str__(self):
        where = "   (" + " ".join(self.mounts) + ")" if self.mounts else ""
        return f"{self.number}:  {self.name}  -  {self.size / 1e9:.1f} GB{where}"


class Bridge(QObject):
    line = Signal(str)
    done = Signal(object, object)
    call = Signal(object)


def link(text):
    label = QLabel(f'<a href="#" style="color:{theme.CYAN}">{text}</a>')
    label.setFont(theme.font(theme.SANS, 9.5))
    label.setTextInteractionFlags(Qt.TextBrowserInteraction)
    label.setCursor(Qt.PointingHandCursor)
    return label


def removable_mounts():
    try:
        from p1401 import linuxdisk  # noqa: PLC0415
        return linuxdisk.removable_mounts()
    except Exception:  # noqa: BLE001 - lsblk missing: no stick is visible, as when Windows lists no removable drive
        return []


def mount_root(path):
    p = os.path.abspath(path)
    while not os.path.ismount(p) and p != os.path.dirname(p):
        p = os.path.dirname(p)
    return p


def mac_name(d):
    return {"25": "Tahoe", "24": "Sequoia", "23": "Sonoma", "22": "Ventura", "21": "Monterey"}.get(d, d)


def is_support_notice(text):
    return text.startswith("NullMoth") or text.startswith("Laptop display note:") or text.startswith("Intel I225-LM vP")


def new_batch():
    return "1401-app-" + secrets.token_hex(8)


def redact_config(xml):
    """The Mac identity OpenCore gives this PC (serial, board serial, UUID, ROM) is the only personal part of a config."""
    keys = r"(<key>(SystemSerialNumber|MLB|BoardSerialNumber|ChassisSerialNumber|SystemUUID|ROM|SerialNumber)</key>\s*"
    xml = re.sub(keys + r"<string>)[^<]*(</string>)", r"\1REMOVED\3", xml)
    return re.sub(keys + r"<data>)[^<]*(</data>)", r"\1AAAAAAAA\3", xml)


def tls_context():
    ctx = ssl.create_default_context()
    try:
        import certifi  # noqa: PLC0415
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        pass
    return ctx


class MainWindow(QMainWindow):
    def __init__(self, verification=False):
        super().__init__()
        self.page = 0
        self.busy = self.scanned = self.built = self.written = self.listing = self.showing_prerequisites = False
        self.prerequisite_return_page = 1
        self.firmware_guide_file = self.scan_dir = self.scan_run_id = self.efi_dir = self.guide_file = None
        self.darwin, self.macos_full, self.summary = "24", "24.99.99", ""
        self.last_send_error = ""
        self.firmware_verification_phase = None
        self.verification = verification
        self.closed = False
        self.bridge = Bridge()
        self.bridge.line.connect(self.say)
        self.bridge.done.connect(self._advance)
        self.bridge.call.connect(lambda fn: fn())
        self.helper = Helper(ask_password=self._ask_password_blocking)

        self.setWindowTitle("1401 Assistant")
        icon = os.path.join(engine.HOME, "share", "icons", "1401.png")
        if os.path.isfile(icon):
            self.setWindowIcon(QIcon(icon))
        self.setFixedSize(900, 620)
        root = QWidget()
        root.setStyleSheet(theme.STYLE)
        root.setFont(theme.body())
        self.setCentralWidget(root)

        head = QWidget(root, objectName="head")
        head.setGeometry(0, 0, 900, 74)
        x = 14
        mark_path = os.path.join(HERE, "moth-mark.jpg")
        if BRANDED and os.path.isfile(mark_path):
            mark = QLabel(head)
            mark.setPixmap(QPixmap(mark_path).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            mark.setGeometry(14, 9, 56, 56)
            x = 80
        brand = QLabel("NullMoth" if BRANDED else "1401", head)
        brand.setFont(theme.brand())
        brand.setStyleSheet(f"color:{theme.PURPLE}")
        brand.setGeometry(x, 4, 400, 40)
        sub = QLabel("1401 Assistant  -  macOS on the PC you already own", head)
        sub.setFont(theme.mono(10, True))
        sub.setStyleSheet(f"color:{theme.CYAN}")
        sub.setGeometry(x + 4, 44, 600, 22)

        nav = QWidget(root, objectName="nav")
        nav.setGeometry(0, 74, 220, 620 - 74 - 56)
        self.side = []
        for i, s in enumerate(STEPS):
            lab = QLabel(f"{i + 1}.  {s}", nav)
            lab.setGeometry(12, 16 + 34 * i, 200, 34)
            self.side.append(lab)

        foot = QWidget(root, objectName="foot")
        foot.setGeometry(0, 620 - 56, 900, 56)
        self.back = QPushButton("< Back", foot)
        self.back.setProperty("secondary", True)
        self.back.setGeometry(560, 12, 150, 32)
        self.next = QPushButton("Continue >", foot)
        self.next.setGeometry(720, 12, 160, 32)
        self.back.clicked.connect(self._on_back)
        self.next.clicked.connect(self.on_next)

        self.body = QWidget(root)
        self.body.setGeometry(220, 74, 680, 620 - 74 - 56)
        b = self.body
        self.title = QLabel(b)
        self.title.setFont(theme.title())
        self.title.setGeometry(24, 14, 620, 40)
        self.note = QLabel(b)
        self.note.setWordWrap(True)
        self.note.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.note.setGeometry(24, 58, 620, 96)
        self.facts = QTreeWidget(b)
        self.facts.setColumnCount(2)
        self.facts.setHeaderHidden(True)
        self.facts.setRootIsDecorated(False)
        self.facts.setFont(theme.mono(9.5))
        self.facts.setColumnWidth(0, 170)
        self.facts.setGeometry(24, 156, 620, 136)
        self.bar = QProgressBar(b)
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setGeometry(24, 300, 620, 18)
        self.log = QPlainTextEdit(b, objectName="log")
        self.log.setReadOnly(True)
        self.log.setFont(theme.mono(9))
        self.log.setGeometry(24, 330, 620, 140)
        self.disks = QComboBox(b)
        self.disks.setFont(theme.mono(10))
        self.disks.setGeometry(24, 170, 500, 28)
        self.refresh = QPushButton("Refresh", b)
        self.refresh.setProperty("secondary", True)
        self.refresh.setGeometry(534, 168, 110, 30)
        self.refresh.clicked.connect(self.fill_disks)
        self.guide = QTextBrowser(b)
        self.guide.document().setDefaultStyleSheet(f"a {{ color: {theme.CYAN}; }}")
        self.guide.setOpenExternalLinks(False)
        self.guide.setOpenLinks(False)
        self.guide.anchorClicked.connect(self._guide_anchor)
        self.guide.setGeometry(24, 110, 620, 352)
        self.oc_link = link("OpenCore Install Guide (dortania.github.io/OpenCore-Install-Guide)")
        self.oc_link.linkActivated.connect(lambda _: QDesktopServices.openUrl(QUrl("https://dortania.github.io/OpenCore-Install-Guide/")))
        self.log_link = link("Something went wrong? Scan for logs and send them")
        self.log_link.linkActivated.connect(lambda _: self.offer_stick_logs(True))
        self.drv_link = link("Update the NVIDIA driver (newest release, here and on your 1401 stick)")
        self.drv_link.linkActivated.connect(lambda _: self.update_driver())
        self.doctor_link = link("Built your own EFI? Check it (and fix it) with the same rules 1401 uses")
        self.doctor_link.linkActivated.connect(lambda _: self.check_own_efi())
        self.firmware_link = link("Review firmware prerequisites")
        self.firmware_link.linkActivated.connect(lambda _: self._firmware_link_clicked())
        self.browser_link = link("Open this page in your web browser")
        self.browser_link.linkActivated.connect(lambda _: self._open_guide_in_browser())
        self.restart_link = link("Restart into BIOS setup")
        self.restart_link.linkActivated.connect(lambda _: self.restart_into_firmware())
        for lab, (lx, ly) in ((self.oc_link, (24, 300)), (self.log_link, (24, 330)), (self.drv_link, (24, 355)),
                              (self.doctor_link, (24, 380)), (self.firmware_link, (24, 300)),
                              (self.browser_link, (24, 466)), (self.restart_link, (400, 466))):
            lab.setParent(b)
            lab.move(lx, ly)
            lab.adjustSize()
        self.go(0)
        if not verification:
            QTimer.singleShot(300, lambda: self.offer_stick_logs(False))

    # ---- async: generator flows whose blocking steps run on worker threads -------------------------------------------
    def spawn(self, gen):
        self._advance(gen, ("ok", None))

    def _advance(self, gen, result):
        try:
            kind, value = result
            job = gen.throw(value) if kind == "err" else gen.send(value)
        except StopIteration:
            return
        except Exception:  # noqa: BLE001
            self._crash(*sys.exc_info())
            return

        def work():
            try:
                res = ("ok", job())
            except Exception as e:  # noqa: BLE001
                res = ("err", e)
            self.bridge.done.emit(gen, res)
        threading.Thread(target=work, daemon=True).start()

    def run_engine(self, args, on_line=None):
        """A job for spawn(): runs the engine; lines go to on_line on the worker thread (default: the log)."""
        sink = on_line or self.bridge.line.emit
        return lambda: engine.run(args, sink, helper=self.helper)

    def _ask_password_blocking(self):
        """Called from a worker thread when only sudo can give root: asks on the window's thread and waits."""
        box, done = {}, threading.Event()

        def ask():
            text, ok = QInputDialog.getText(self, "1401 - administrator password",
                                            "1401 needs administrator rights to read this PC's ACPI tables and to "
                                            "write the stick.\nYour password (for sudo):", QLineEdit.Password)
            box["value"] = text if ok else None
            done.set()
        self.bridge.call.emit(ask)
        done.wait()
        return box.get("value")

    # ---- the page machinery ------------------------------------------------------------------------------------------
    def say(self, s):
        if s.startswith("PROGRESS "):
            parts = s.split(" ")
            if len(parts) > 1 and parts[1].lstrip("-").isdigit():
                self.bar.setValue(max(0, min(100, int(parts[1]))))
            return
        self.log.appendPlainText(s)

    def show_only(self, *on):
        for c in (self.facts, self.bar, self.log, self.disks, self.refresh, self.guide, self.oc_link, self.log_link):
            c.setVisible(c in on)
        self.drv_link.setVisible(self.log_link.isVisible())
        self.doctor_link.setVisible(self.log_link.isVisible())
        self.browser_link.setVisible(self.guide in on)
        self.restart_link.setVisible(self.guide in on and self.page == 3 and not self.showing_prerequisites
                                     and os.path.isdir("/sys/firmware/efi"))
        self.firmware_link.setVisible(self.page in (1, 2) and self.firmware_guide_file is not None
                                      and os.path.isfile(self.firmware_guide_file))
        self.firmware_link.setEnabled(not self.busy)

    def go(self, p):
        if self.busy or p < 0 or p >= len(STEPS):
            return
        self.page = p
        for i, lab in enumerate(self.side):
            color = theme.PURPLE if i == p else (theme.CYAN if i < p else theme.MUTED)
            f = theme.font(theme.SANS, 9.75, i == p)
            lab.setFont(f)
            lab.setStyleSheet(f"color:{color}")
        self.title.setText(STEPS[p])
        # Text-only pages get the room the lists use elsewhere (the note would be cut off at 96 px).
        self.note.setFixedHeight(232 if p in (0, 5) else 96)
        self.back.setEnabled(0 < p < len(STEPS) - 1)
        self.next.setText("Continue >")
        if p == 0:
            self.note.setText(
                "The 1401 Assistant helps you install macOS on this PC, the way Boot Camp put Windows on a Mac.\n\n"
                "It checks this PC, builds the startup files macOS needs for your exact hardware, shows BIOS settings based on "
                "your scan and startup files, and makes a macOS install stick. Linux stays as it is. Nothing about this PC is "
                "sent anywhere.\n\nYou need: an internet connection and a USB stick of 4 GB or more that can be erased.\n\n"
                "1401 is new and may not work on every PC. If it does not work on yours, set up OpenCore by hand with the "
                "guide below; the NullMoth app works on any OpenCore setup.")
            self.show_only(self.oc_link, self.log_link)
            miss = engine.missing()
            if miss:
                self.note.setText(miss)
                self.next.setEnabled(False)
            else:
                self.next.setEnabled(True)
        elif p == 1:
            self.note.setText("This PC was checked. Continue, or check again." if self.scanned else
                              "1401 reads this PC's hardware (processor, board, graphics, network, storage) and its ACPI "
                              "tables. This takes about a minute. Linux asks for your password: reading the ACPI tables "
                              "needs administrator rights.")
            self.next.setText("Continue >" if self.scanned else "Check this PC")
            self.show_only(self.facts, self.log)
            self.next.setEnabled(True)
        elif p == 2:
            self.note.setText(self.summary.split("\n")[0] if self.built else
                              "1401 now picks the newest macOS your hardware runs and builds the startup files (OpenCore EFI) "
                              "for it, then checks them with OpenCore's own validator. Downloads OpenCore and drivers.")
            self.next.setText("Continue >" if self.built else "Build")
            self.show_only(self.facts, self.log)
            self.next.setEnabled(self.scanned)
            if self.built:
                self.log.setPlainText(self.summary)
                self.log.moveCursor(self.log.textCursor().MoveOperation.Start)
        elif p == 3:
            if self.showing_prerequisites:
                self.title.setText("Firmware prerequisites")
                self.note.setText("Review storage requirements before building. No EFI or firmware changes are made by this "
                                  "page. Return to the check or build when finished.")
                self.show_only(self.guide)
                self.next.setEnabled(True)
                self.next.setText("Return >")
                if self.firmware_guide_file and os.path.isfile(self.firmware_guide_file):
                    self._navigate(self.firmware_guide_file)
            else:
                self.note.setText("These BIOS suggestions follow your scan and startup files. Check menu names in the exact "
                                  "board manual, then take a photo before restarting.")
                self.show_only(self.guide)
                self.next.setEnabled(self.built)
                if self.guide_file and os.path.isfile(self.guide_file):
                    self._navigate(self.guide_file)
        elif p == 4:
            self.note.setText("Plug in the USB stick that will become the macOS installer. EVERYTHING on it is erased.\n"
                              "1401 downloads macOS from Apple onto it and adds the startup files" +
                              (" and the NullMoth NVIDIA driver." if "NVIDIA" in self.summary else "."))
            self.next.setText("Continue >" if self.written else "Erase and create")
            self.show_only(self.disks, self.refresh, self.bar, self.log)
            self.next.setEnabled(True)
            self.fill_disks()
        elif p == 5:
            self.note.setText("The macOS stick is ready.\n\n1. Restart and open your board's boot menu (the key is in your BIOS "
                              "steps), then choose the stick.\n2. In the 1401 boot menu choose \"Install macOS\" and follow "
                              "Apple's installer.\n3. When macOS is running, open the NullMoth folder on the stick and run "
                              "1401.app to finish the NVIDIA driver.")
            self.next.setText("Close")
            self.show_only()
            self.next.setEnabled(True)

    def _navigate(self, path):
        self.guide.setSource(QUrl.fromLocalFile(path))
        self.guide.scrollToAnchor("g")

    def _guide_anchor(self, url):
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
        elif url.hasFragment():
            self.guide.scrollToAnchor(url.fragment())

    def _open_guide_in_browser(self):
        path = self.firmware_guide_file if self.showing_prerequisites else self.guide_file
        if path and os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_back(self):
        if self.page == 3 and self.showing_prerequisites:
            self.showing_prerequisites = False
            self.go(self.prerequisite_return_page)
        else:
            self.go(self.page - 1)

    def _firmware_link_clicked(self):
        if self.busy or self.firmware_guide_file is None or not os.path.isfile(self.firmware_guide_file):
            return
        self.prerequisite_return_page = self.page
        self.showing_prerequisites = True
        self.go(3)

    def guide_flags(self):
        return ["--host", "linux"] + ([] if BRANDED else ["--unbranded"])

    def prepare_firmware_guide(self):
        """A sub-flow (yield from it): the firmware prerequisite page for this scan."""
        self.firmware_guide_file = None
        if not self.scanned or not self.scan_dir or not self.scan_run_id:
            return
        target = os.path.join(engine.WORK, "Firmware-prerequisites-" + self.scan_run_id + ".html")
        self.busy = True
        self.next.setEnabled(False)
        try:
            rc = yield self.run_engine(["p1401.guide", "--bios-only", os.path.join(self.scan_dir, "Report.json"),
                                        "--run-id", self.scan_run_id, "--html", target] + self.guide_flags())
            self.report_save_status()
            if rc == 0 and os.path.isfile(target):
                self.firmware_guide_file = target
            else:
                self.say("Firmware review could not be generated. No EFI or firmware was changed. The build refusal still "
                         "includes storage preparation guidance.")
        except Exception:  # noqa: BLE001
            self.say("Firmware review is unavailable. No EFI or firmware was changed; use the storage guidance in the build refusal.")
        finally:
            self.busy = False
            self.next.setEnabled(True)

    def on_next(self):
        if self.busy:
            return
        if self.page == 3 and self.showing_prerequisites:
            self.showing_prerequisites = False
            self.go(self.prerequisite_return_page)
            return
        if self.page == 0:
            self.go(1)
            return
        if self.page == 1 and not self.scanned:
            self.spawn(self._scan_flow())
            return
        if self.page == 2 and not self.built:
            self.spawn(self._build_flow())
            return
        if self.page == 4 and not self.built:
            self.go(2)
            return
        if self.page == 4 and not self.written:
            self._write_start()
            return
        if self.page == 5:
            self.close()
            return
        self.go(self.page + 1)

    def _scan_flow(self):
        self.busy = True
        self.next.setEnabled(False)
        self.log.clear()
        self.scan_run_id = uuid.uuid4().hex
        self.scan_dir = scanevidence.run_directory(engine.WORK, self.scan_run_id)
        os.makedirs(engine.WORK, exist_ok=True)
        scanevidence.remember(engine.WORK, self.scan_run_id)
        self.say("Checking this PC...")
        rc = yield self.run_engine(["p1401.scan", self.scan_dir, "--run-id", self.scan_run_id])
        self.report_save_status()
        self.busy = False
        self.next.setEnabled(True)
        if rc != 0:
            self.say("The check failed. The lines above say why.")
            self.offer_operation_report("check")
            return
        self.scanned = True
        self.load_facts()
        yield from self.prepare_firmware_guide()
        self.say("Done.")
        self.go(1)

    def _build_flow(self):
        self.busy = True
        self.next.setEnabled(False)
        self.log.clear()
        self.efi_dir = os.path.join(engine.WORK, "efi")
        rep, acpi = os.path.join(self.scan_dir, "Report.json"), os.path.join(self.scan_dir, "ACPI")
        lines = []
        self.say("Building the startup files for this PC...")
        # A stick that already failed to boot says why in its newest OpenCore log; the build reads it (bootfix.py).
        boot_logs = []
        for root, _ in removable_mounts():
            try:
                newest = sorted(glob.glob(os.path.join(root, "opencore-*.txt")), reverse=True)[:1]
                boot_logs += [x for f in newest for x in ("--boot-log", f)]
                # macOS saves a kernel panic on the stick (ApplePanic); its backtrace names the kext to switch off.
                for pf in sorted(glob.glob(os.path.join(root, "panic-*.txt")), reverse=True)[:2]:
                    boot_logs += ["--boot-log", pf]
            except OSError:
                pass
        stopped = os.path.join(engine.WORK, "stopped-at.txt")
        try:
            if os.path.isfile(stopped):
                with open(stopped, encoding="utf-8") as fh:
                    boot_logs += ["--stopped-at", fh.read().strip()]
        except OSError:
            pass
        if boot_logs:
            self.say("Using the last startup log on the stick to adjust this build.")
        rc = yield self.run_engine(["p1401", "build", rep, acpi, self.efi_dir, "--json"] + boot_logs, lines.append)
        self.report_save_status()
        self.busy = False
        self.next.setEnabled(True)
        text = "\n".join(lines)
        start = next((i for i, l in enumerate(lines) if l.lstrip().startswith("{")), None)
        try:
            r = json.loads("\n".join(lines[start:])) if start is not None else None
            if not isinstance(r, dict):
                raise ValueError("no build result")
            if rc != 0 or r.get("ok") is not True:
                detail = str(r.get("error") or "")
                self.say("The build stopped: " + (detail if detail.strip() else f"Engine exit code {rc}."))
                self.offer_operation_report("build")
                return
            mv = str(r["macos_version"])
            self.macos_full, self.darwin = mv, mv.split(".")[0]
            self.summary = (f"macOS {mac_name(self.darwin)} for this PC, as a {r['smbios']}. The startup files passed "
                            "OpenCore's own check.")
            for n in r.get("notices") or []:
                if is_support_notice(str(n)):
                    self.summary += "\n" + str(n)
            self.built = True
            self.showing_prerequisites = False
        except Exception as e:  # noqa: BLE001
            self.say(f"Could not read the build result ({type(e).__name__}). The saved operation report includes the engine output.")
            for line in text.splitlines()[:40]:
                self.say(line)
            self.offer_operation_report("build")
            return
        self.guide_file = os.path.join(engine.WORK, "BIOS-steps.html")
        yield self.run_engine(["p1401.guide", rep, os.path.join(self.efi_dir, "EFI", "OC", "config.plist"),
                               "--macos", self.macos_full, "--html", self.guide_file] + self.guide_flags())
        self.report_save_status()
        self.say("Done.")
        self.go(2)

    def _write_start(self):
        d = self.disks.currentData()
        if d is None:
            self.message("Plug in a USB stick and press Refresh.")
            return
        ok = QMessageBox.warning(self, "1401 - erase this stick?",
                                 f"Erase {d} and make it the macOS installer?\n\nEverything on it will be lost.",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self.spawn(self._write_flow(d))

    def _write_flow(self, d):
        self.busy = True
        self.next.setEnabled(False)
        self.refresh.setEnabled(False)
        self.log.clear()
        self.bar.setValue(0)
        args = ["p1401.usbwriter", "write", d.number, self.efi_dir, self.darwin]
        pkg = sorted(glob.glob(os.path.join(engine.NULLMOTH_DIR, "nullmoth-nvidia-*.tar.gz")))
        if pkg:
            args += ["--driver", pkg[0]]
        mac = sorted(glob.glob(os.path.join(engine.NULLMOTH_DIR, "1401-Mac-*.zip")))
        if mac:
            args += ["--extra", mac[0]]
        report = os.path.join(self.scan_dir, "Report.json")
        if os.path.isfile(report):
            args += ["--profile", report]
        if d.size > 256 * 1024 ** 3:
            args += ["--allow-large"]
        rc = yield self.run_engine(args)
        self.report_save_status()
        self.busy = False
        self.next.setEnabled(True)
        self.refresh.setEnabled(True)
        if rc != 0:
            self.say("Writing the stick did not finish. The saved report includes the writer output; partial changes to "
                     "the selected stick may remain.")
            self.offer_operation_report("write")
            return
        self.written = True
        self.bar.setValue(100)
        self.go(5)

    def load_facts(self):
        self.facts.clear()
        try:
            with open(os.path.join(self.scan_dir, "Report.json"), encoding="utf-8") as fh:
                r = json.load(fh)

            def add(k, v):
                self.facts.addTopLevelItem(QTreeWidgetItem([k, v]))
            mb, cpu = r.get("Motherboard"), r.get("CPU")
            if isinstance(mb, dict):
                add("Board", str(mb.get("Name", "")))
            if isinstance(cpu, dict):
                add("Processor", str(cpu.get("Processor Name") or cpu.get("Manufacturer", "")))
            for g in (r.get("GPU") or {}):
                add("Graphics", g)
            for n in (r.get("Network") or {}):
                add("Network", n)
        except Exception as e:  # noqa: BLE001
            self.say("Could not read the report: " + str(e))

    def self_disk_mount(self):
        return mount_root(engine.HOME)

    def fill_disks(self):
        if self.listing:
            return
        self.spawn(self._fill_disks_flow())

    def _fill_disks_flow(self):
        self.listing = True
        self.disks.clear()
        self.refresh.setEnabled(False)
        next_was = self.next.isEnabled()
        self.next.setEnabled(False)
        self.say("Looking for USB sticks...")
        skipped, box = [], {}

        def listing():
            from p1401 import linuxdisk  # noqa: PLC0415
            t = threading.Thread(target=lambda: box.update(v=linuxdisk.list_disks()), daemon=True)
            t.start()
            t.join(30)
            if t.is_alive():
                raise TimeoutError("lsblk did not answer within 30 seconds")
            return box.get("v", [])
        err, found = None, []
        try:
            found = yield listing
        except TimeoutError:
            err = "Linux did not answer the USB disk list within 30 seconds. Unplug card readers and other USB drives, then press Refresh."
        except Exception as e:  # noqa: BLE001
            err = "Could not list USB sticks: " + str(e)
        home = self.self_disk_mount()
        count = 0
        for raw in found or []:
            d = UsbDisk(raw)
            what = f"{d.number} {d.name} ({d.size / 1e9:.1f} GB)"
            if raw.get("BusType") != "USB":
                skipped.append(f"{what}: connected by {raw.get('BusType')}, not USB")
                continue
            if raw.get("IsSystem") or raw.get("IsBoot"):
                skipped.append(f"{what}: Linux runs from it")
                continue
            if home != "/" and home in d.mounts:
                skipped.append(f"{d}: 1401 is running from it")
                continue
            if d.size < MIN_STICK:
                skipped.append(f"{d}: too small, 1401 needs 2 GB")
                continue
            self.disks.addItem(str(d), d)
            count += 1
        if err:
            self.say(err)
        if count:
            self.disks.setCurrentIndex(0)
            self.say(f"Found {count} USB stick(s).")
        elif not err:
            self.say("No USB stick found. Plug one in (4 GB or more, not the stick 1401 runs from) and press Refresh.")
            if skipped:
                self.say("Disks 1401 saw and left out:")
                for w in skipped:
                    self.say("  " + w)
        self.listing = False
        self.refresh.setEnabled(not self.busy)
        if self.page == 4:
            self.next.setEnabled(next_was and not self.busy)

    # ---- reports ----------------------------------------------------------------------------------------------------
    def report_save_status(self):
        r = engine.last_report
        if r is not None and not r.saved:
            self.say(r.failure + " The operation output may remain visible, but a durable final report is unavailable.")

    def offer_operation_report(self, kind):
        r = engine.last_report
        if r is None or not r.saved:
            self.say("The operation has no saved local report. The visible output remains available to copy." if r is None else r.failure)
            return
        self.say("The local report is saved at " + r.path + ". Scan for logs and send them can retry after restart.")
        self.offer_log(r.path, kind)

    def message(self, text, title="1401", icon=QMessageBox.Information):
        box = QMessageBox(icon, title, text, QMessageBox.Ok, self)
        box.exec()

    def offer_log(self, path, kind):
        ask = QMessageBox.information(
            self, "1401 - sending the log",
            f"The {kind} failed.\n\n1401 is sending this log to nullmothsystems.com so the bug can be found and fixed. It holds "
            "this scan's hardware list, bounded device/CPU observations and what the check or build printed. Linux "
            "capabilities do not establish macOS support. Your Linux user name and PC name are removed first. Nothing else "
            "on this PC is sent.\n\nClick OK to send it now, or Cancel to keep it only on this PC.",
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok)
        if ask != QMessageBox.Ok:
            self.say("The log was not sent. It stays in " + path + ".")
            return
        ids, batch = [], new_batch()
        i = self.send(path, f"1401-{kind}-log.txt", kind + " failed", batch)
        if i:
            ids.append(i)
        evidence = scanevidence.find(engine.WORK, self.scan_run_id, False)
        if evidence:
            receipt = self.send(evidence.path, "1401-scan-evidence.json.txt",
                                kind + " failed; saved evidence from this scan run; macOS support not assessed", batch)
            if receipt:
                ids.append(receipt)
        self.say("Logs sent. Report IDs " + ", ".join(ids) + " - mention them in the NullMoth Discord if you ask for help."
                 if ids else "Could not send the logs (" + self.last_send_error + "). They stay on this PC.")

    def send(self, path, send_name, what, batch=None):
        """POST one text file to the site's upload endpoint; returns the report ID, or None (reason in last_send_error)."""
        self.last_send_error = ""
        try:
            with open(path, "rb") as fh:
                body = fh.read()
            # the site refuses a "text log" with NUL bytes; OpenCore's file log can carry padding NULs at the end
            if send_name.lower().endswith(".txt"):
                body = body.replace(b"\0", b"")
            sha = hashlib.sha256(body).hexdigest()
            notes = ("1401 " + engine.product_version() + " " + what + " (sent from the app)")[:3900]
            meta = json.dumps({"consent": True, "notes": notes, "batch": batch or new_batch()})
            b64 = base64.urlsafe_b64encode(meta.encode()).decode().rstrip("=")
            req = urllib.request.Request(UPLOAD, data=body, method="POST", headers={
                "Content-Type": "application/octet-stream", "X-File-Name": urllib.request.quote(send_name),
                "X-Meta": b64, "X-Content-SHA256": sha, "User-Agent": "1401-linux"})
            with urllib.request.urlopen(req, timeout=30, context=tls_context()) as resp:
                j = json.loads(resp.read().decode("utf-8", "replace") or "{}")
            if "sha256" in j and str(j["sha256"]) != sha:
                self.last_send_error = "the server stored different bytes"
                return None
            return str(j.get("id", "?"))
        except urllib.error.HTTPError as he:
            self.last_send_error = str(he)
            try:
                j = json.loads(he.read().decode("utf-8", "replace"))
                if "error" in j:
                    self.last_send_error = str(j["error"])
            except Exception:  # noqa: BLE001
                pass
            return None
        except Exception as e:  # noqa: BLE001
            self.last_send_error = str(getattr(e, "reason", e))
            return None

    def offer_stick_logs(self, asked):
        if self.closed or (not self.isVisible() and not asked):
            return
        found, configs = [], []
        try:
            found += [f for f in durable.find(engine.WORK) if asked or "-crash-" in os.path.basename(f)]
        except Exception as error:  # noqa: BLE001
            self.say(f"Saved reports could not be listed ({type(error).__name__}). Check access to the app data folder; "
                     "existing files were retained.")
        bl = os.path.join(engine.WORK, "build-log.txt")
        if asked and os.path.isfile(bl):
            found.append(bl)
        scan_log = os.path.join(engine.WORK, "scan-log.txt")
        if asked and os.path.isfile(scan_log):
            found.append(scan_log)
        evidence = scanevidence.find(engine.WORK, self.scan_run_id, True) if asked else None
        if evidence:
            found.insert(0, evidence.path)
        crash = os.path.join(engine.WORK, "crash-log.txt")
        if os.path.isfile(crash):
            found.append(crash)
        stick_roots = {}
        for root, disk in removable_mounts():
            try:
                here = sorted(glob.glob(os.path.join(root, "panic-*.txt")))
                here += sorted(glob.glob(os.path.join(root, "opencore-*.txt")), reverse=True)[:3]
                found += here
                for f in here:
                    stick_roots[f] = root
                # The config that booted goes with the stick's logs, serials removed.
                cfg = os.path.join(root, "EFI", "OC", "config.plist")
                if here and os.path.isfile(cfg):
                    os.makedirs(engine.WORK, exist_ok=True)
                    safe = os.path.join(engine.WORK, f"stick-config-{disk}.txt")
                    with open(cfg, encoding="utf-8", errors="replace") as fh:
                        text = redact_config(fh.read())
                    with open(safe, "w", encoding="utf-8") as fh:
                        fh.write(text)
                    configs.append(safe)
            except OSError:
                pass
        if not found:
            if asked:
                self.message("No logs were found. Plug in the USB stick 1401 made (the one you started macOS from) and try again.")
            return
        local = [f for f in found if durable.is_local(engine.WORK, f)]
        other = [f for f in found if not durable.is_local(engine.WORK, f)]
        ordered = []
        for f in local + configs + other:
            if f not in ordered:
                ordered.append(f)
        found = ordered[:6]   # the site takes 30 uploads per hour from one address
        ok, screen = self.ask_send(
            f"1401 found {len(found)} diagnostic file{'' if len(found) == 1 else 's'} (available scan/build evidence and "
            "startup logs from attached sticks).\n\nThese retain available failure and hardware observations. 1401 is "
            "sending them to nullmothsystems.com so the bug can be found and fixed. They hold what OpenCore and macOS printed "
            "while starting, and the stick's OpenCore settings with serial numbers removed. An included scan receipt records "
            "saved hardware observations, even if the check failed; it does not prove this PC or the attached stick has that "
            "hardware. Nothing else on this PC is sent.\n\nClick Send to send them now, or Cancel to keep them locally.")
        if not ok:
            return
        what = "startup log from the stick" + ("; screen stopped at: " + screen if screen else "")
        # the next build reads it too (bootfix.after_handoff): a hang after OpenCore leaves no file, only this line
        if screen:
            try:
                os.makedirs(engine.WORK, exist_ok=True)
                with open(os.path.join(engine.WORK, "stopped-at.txt"), "w", encoding="utf-8") as fh:
                    fh.write(screen)
            except OSError:
                pass
        ids, batch = [], new_batch()
        for f in found:
            local_what = ("saved local operation report; original app version is recorded in the payload; not current-PC "
                          "identity proof") if durable.is_local(engine.WORK, f) else what
            if evidence and f == evidence.path:
                local_what += ("; this scan run; not macOS qualification" if evidence.current_run else
                               "; saved prior scan; not current-PC or attached-stick identity proof")
            name = os.path.basename(f)
            i = self.send(f, name + ".txt" if name.lower().endswith(".json") else name, local_what, batch)
            if i is None:
                continue
            ids.append(i)
            try:
                if f in (bl, scan_log) or durable.is_local(engine.WORK, f) or (evidence and f == evidence.path) or f in configs:
                    continue
                if f == crash:
                    os.replace(crash, crash + ".sent-" + time.strftime("%Y%m%d-%H%M%S"))
                    continue
                sent = os.path.join(stick_roots.get(f, mount_root(f)), "NullMoth", "sent-logs")
                os.makedirs(sent, exist_ok=True)
                shutil.move(f, os.path.join(sent, os.path.basename(f)))
            except OSError:
                pass
        self.message(f"Sent {len(ids)} log(s). Report ID {', '.join(ids)} - mention it in the NullMoth Discord if you ask for help."
                     if ids else "The logs could not be sent: " + self.last_send_error +
                     "\nThey are still where they were; try again with Scan for logs and send them.")

    def ask_send(self, text):
        d = QDialog(self)
        d.setWindowTitle("1401 - sending the startup logs")
        d.setFixedSize(520, 330)
        d.setStyleSheet(theme.STYLE)
        d.setFont(theme.body())
        msg = QLabel(text, d)
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        msg.setGeometry(14, 12, 492, 180)
        q = QLabel("Optional: the last line on the screen when it stopped (for example \"PCI configuration begin\"):", d)
        q.setWordWrap(True)
        q.setGeometry(14, 196, 492, 36)
        box = QLineEdit(d)
        box.setMaxLength(300)
        box.setGeometry(14, 236, 492, 28)
        send = QPushButton("Send", d)
        send.setGeometry(330, 282, 84, 30)
        send.setDefault(True)
        cancel = QPushButton("Cancel", d)
        cancel.setProperty("secondary", True)
        cancel.setGeometry(422, 282, 84, 30)
        send.clicked.connect(d.accept)
        cancel.clicked.connect(d.reject)
        r = d.exec()
        screen = box.text().replace("\r", " ").replace("\n", " ").strip()
        return r == QDialog.Accepted, screen

    # ---- links --------------------------------------------------------------------------------------------------------
    def update_driver(self):
        if self.busy:
            return
        self.spawn(self._update_driver_flow())

    def _update_driver_flow(self):
        self.busy = True
        self.drv_link.setEnabled(False)
        dirs = [engine.NULLMOTH_DIR]
        for root, _ in removable_mounts():
            if os.path.isdir(os.path.join(root, "NullMoth")):
                dirs.append(os.path.join(root, "NullMoth"))
        lines = []
        rc = yield self.run_engine(["p1401.nullmoth", "update"] + dirs, lines.append)
        self.report_save_status()
        self.busy = False
        self.drv_link.setEnabled(True)
        text = "\n".join(l for l in lines if not l.startswith("PROGRESS "))
        self.message(("The NVIDIA driver is up to date.\n\n" + text +
                      ("" if len(dirs) > 1 else "\n\nNo 1401 stick was plugged in; plug it in and run this again to update it too."))
                     if rc == 0 else "The driver could not be updated:\n\n" + text, "1401 - Update driver")

    def check_own_efi(self):
        if self.busy:
            return
        root = QFileDialog.getExistingDirectory(self, "Select the drive or folder that holds your EFI folder")
        if not root:
            return
        self.spawn(self._efidoctor_flow(root))

    def _efidoctor_flow(self, root):
        args = ["p1401.efidoctor", root]
        rep = os.path.join(self.scan_dir, "Report.json") if self.scan_dir else None
        if rep and os.path.isfile(rep):
            args += ["--report", rep]
        newest = sorted(glob.glob(os.path.join(mount_root(root), "opencore-*.txt")), reverse=True)[:1]
        if newest:
            args += ["--boot-log", newest[0]]
        self.busy = True
        lines = []
        rc = yield self.run_engine(args, lines.append)
        self.busy = False
        text = "\n".join(l for l in lines if l.startswith("  "))
        if rc == 0:
            self.message("Your EFI passed every check 1401 makes.\n\n" + text)
            return
        if not any(l.startswith("  needs fixing") for l in lines):
            self.message("1401 found problems it cannot fix by itself:\n\n" + text, icon=QMessageBox.Warning)
            return
        if QMessageBox.question(self, "1401", text + "\n\nFix these in your config.plist now? The current one is kept as a "
                                "backup next to it.") != QMessageBox.Yes:
            return
        self.busy = True
        lines.clear()
        rc = yield self.run_engine(args + ["--fix"], lines.append)
        self.busy = False
        self.message("\n".join(l for l in lines if l.startswith("  ")), icon=QMessageBox.Information if rc == 0 else QMessageBox.Warning)

    def restart_into_firmware(self):
        """What the BIOS steps page asks for: restart straight into the firmware's setup (systemd, UEFI only)."""
        if QMessageBox.question(self, "1401 - restart into BIOS setup",
                                "Restart now and open the BIOS setup?\n\nSave your work first. Take a photo of the BIOS "
                                "steps before you restart: you can't see them while you're in the BIOS.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if not shutil.which("systemctl"):
            self.message("This Linux has no systemctl. Restart and press the setup key shown in the steps.", icon=QMessageBox.Warning)
            return
        p = subprocess.run(["systemctl", "reboot", "--firmware-setup"], capture_output=True, text=True)
        if p.returncode != 0:
            self.message("Linux could not restart into the BIOS setup (" + (p.stderr or p.stdout).strip()[-300:] +
                         ").\nRestart and press the setup key shown in the steps.", icon=QMessageBox.Warning)

    # ---- crashes and closing ------------------------------------------------------------------------------------------
    def _crash(self, etype, value, tb):
        report = durable.save(engine.WORK, "crash", engine.product_version(),
                              "time: " + time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()) + "\n" +
                              " ".join(os.uname()) + "\n\n" + "".join(traceback.format_exception(etype, value, tb)))
        self.message(durable.clean(str(value)) + "\n\n" + (
            "A local crash report was saved at " + report.path + ". Scan for logs and send them can retry after restart."
            if report.saved else report.failure), icon=QMessageBox.Critical)

    def closeEvent(self, event):  # noqa: N802 - Qt's name
        self.closed = True
        self.helper.close()
        super().closeEvent(event)

    # ---- the packaged verification (verify.py) --------------------------------------------------------------------------
    def verify_support_notice_presentation(self):
        self.summary = ("Package verification\nLaptop display note: panel wiring requires verification.\n"
                        "Intel I225-LM vP link testing is required.")
        self.built = self.scanned = True
        self.go(2)
        return self.log.isVisible() and self.log.toPlainText() == self.summary

    def verify_firmware_prerequisite_navigation(self, app):
        saved = (self.page, self.prerequisite_return_page, self.scanned, self.built, self.showing_prerequisites,
                 self.written, self.busy, self.firmware_guide_file)
        directory = os.path.join(os.environ.get("TMPDIR", "/tmp"), "1401-firmware-ui-" + uuid.uuid4().hex)
        os.makedirs(directory)
        local = os.path.join(directory, "firmware.html")
        try:
            with open(local, "w", encoding="utf-8") as fh:
                fh.write("<!doctype html><html><head><title>Firmware prerequisite fixture</title></head><body><h1>Firmware "
                         "prerequisites</h1><p>No settings changed.</p></body></html>")
            ph = "missing_guide"
            self.firmware_verification_phase = ph
            self.busy, self.scanned, self.built, self.written, self.showing_prerequisites = False, True, False, False, False
            self.firmware_guide_file = None
            self.go(1)
            if self.firmware_link.isVisibleTo(self):
                return False
            self.firmware_verification_phase = "scan_link_and_local_document"
            self.firmware_guide_file = local
            self.go(1)
            if not self.firmware_link.isVisibleTo(self) or not self.firmware_link.isEnabled() or \
                    self.firmware_link.geometry().bottom() > self.body.height():
                return False
            self._firmware_link_clicked()
            if self.page != 3 or not self.showing_prerequisites or self.prerequisite_return_page != 1 or \
                    self.title.text() != "Firmware prerequisites" or self.next.text() != "Return >" or \
                    not self.guide.isVisibleTo(self) or not self.next.isEnabled() or self.built:
                return False
            deadline = time.time() + 2.5
            while time.time() < deadline and self.guide.documentTitle() != "Firmware prerequisite fixture":
                app.processEvents()
                time.sleep(0.01)
            if not self.guide.source().isLocalFile() or os.path.abspath(self.guide.source().toLocalFile()) != local or \
                    self.guide.documentTitle() != "Firmware prerequisite fixture":
                return False
            self.firmware_verification_phase = "scan_next_return"
            self.next.click()
            if self.page != 1 or self.showing_prerequisites or self.built or self.written or not self.firmware_link.isVisibleTo(self):
                return False
            self.firmware_verification_phase = "scan_back_return"
            self._firmware_link_clicked()
            self.back.click()
            if self.page != 1 or self.showing_prerequisites or self.built or self.written:
                return False
            self.firmware_verification_phase = "refused_build_link"
            self.go(2)
            if not self.firmware_link.isVisibleTo(self) or self.built:
                return False
            self._firmware_link_clicked()
            if self.page != 3 or self.prerequisite_return_page != 2 or self.built:
                return False
            self.firmware_verification_phase = "refused_build_next_return"
            self.next.click()
            if self.page != 2 or self.showing_prerequisites or self.built or self.written:
                return False
            self.firmware_verification_phase = "refused_build_back_return"
            self._firmware_link_clicked()
            self.back.click()
            if self.page != 2 or self.showing_prerequisites or self.built or self.written:
                return False
            self.firmware_verification_phase = "successful_build_required"
            self.go(3)
            if self.next.isEnabled() or self.title.text() != "Your BIOS steps" or self.built:
                return False
            self.built = True
            self.go(3)
            if not self.next.isEnabled() or self.next.text() != "Continue >" or self.title.text() != "Your BIOS steps":
                return False
            self.built = False
            self.go(2)
            # The real Next handler with synthetic page state; not go(4), which lists real disks.
            self.firmware_verification_phase = "unbuilt_usb_refusal"
            self.page = 4
            self.next.click()
            if self.page != 2 or self.built or self.written or self.disks.isVisibleTo(self) or self.refresh.isVisibleTo(self):
                return False
            self.firmware_verification_phase = "busy_link_refusal"
            self.busy = True
            self._firmware_link_clicked()
            if self.page != 2 or self.showing_prerequisites:
                return False
            self.firmware_verification_phase = "missing_file_link_refusal"
            self.busy = False
            self.firmware_guide_file = os.path.join(directory, "missing.html")
            self.go(2)
            self._firmware_link_clicked()
            passed = self.page == 2 and not self.firmware_link.isVisibleTo(self) and not self.showing_prerequisites \
                and not self.built and not self.written
            if passed:
                self.firmware_verification_phase = "complete"
            return passed
        finally:
            self.busy = False
            self.guide.clear()
            (page, ret, self.scanned, self.built, self.showing_prerequisites, self.written, busy,
             self.firmware_guide_file) = saved
            self.prerequisite_return_page = ret
            self.go(page)
            self.busy = busy
            shutil.rmtree(directory, ignore_errors=True)
