"""Starts helper.py as root once per session and runs the root steps through it.

pkexec (polkit) is used when it is installed: the desktop's own password window appears. Without pkexec, sudo is
asked with the password the window collects. If neither works, the step stops with what to do instead."""
import json
import os
import shutil
import subprocess
import sys
import threading

from . import engine

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helper.py")


class NotGranted(RuntimeError):
    pass


class Helper:
    def __init__(self, ask_password=None):
        self.ask_password = ask_password  # () -> str | None, called from a worker thread
        self.proc = None
        self.lock = threading.Lock()

    def _command(self):
        return [sys.executable, "-I", HELPER, "--app", engine.APP_DIR, "--work", engine.WORK,
                "--uid", str(os.getuid()), "--gid", str(os.getgid())]

    def _ready(self, proc, timeout=300):
        """Waits for {"ready": true}; anything else (a refused password, a closed polkit window) is a refusal."""
        result = {}

        def read():
            for raw in iter(proc.stdout.readline, b""):
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("ready"):
                    result["ok"] = True
                    return
                if msg.get("error"):
                    result["error"] = msg["error"]
                    return
        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(timeout)
        if not result.get("ok"):
            try:
                proc.kill()
            except OSError:
                pass
            err = b""
            try:
                err = proc.stderr.read() or b""
            except (OSError, ValueError):
                pass
            raise NotGranted(result.get("error") or err.decode("utf-8", "replace").strip()[-300:] or "not granted")

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        cmd = self._command()
        tried = []
        if shutil.which("pkexec") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            proc = subprocess.Popen(["pkexec"] + cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                self._ready(proc)
                self.proc = proc
                return
            except NotGranted as e:
                tried.append("polkit: " + (str(e) or "not granted"))
                if proc.returncode in (126, 127) and "dismiss" in str(e).lower():
                    raise NotGranted("Administrator rights were not granted (the password window was closed).") from None
        if shutil.which("sudo") and self.ask_password is not None:
            password = self.ask_password()
            if password is None:
                raise NotGranted("Administrator rights were not granted.")
            proc = subprocess.Popen(["sudo", "-S", "-p", "", "-k", "--"] + cmd, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.stdin.write(password.encode() + b"\n")
            proc.stdin.flush()
            try:
                self._ready(proc, timeout=30)
                self.proc = proc
                return
            except NotGranted as e:
                tried.append("sudo: " + (str(e) or "refused"))
        raise NotGranted("1401 needs administrator rights for this step and could not get them (" + "; ".join(tried or
                         ["neither pkexec nor sudo is installed"]) + "). Install polkit (pkexec), or start 1401 with "
                         "sudo -E from a terminal.")

    def run(self, args, on_line):
        with self.lock:
            try:
                self.start()
            except NotGranted as e:
                on_line(str(e))
                return 1
            env = {k: os.environ[k] for k in ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                                              "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "P1401_TEST_LOOP_AS_USB")
                   if k in os.environ}
            try:
                self.proc.stdin.write((json.dumps({"argv": list(args), "env": env}) + "\n").encode())
                self.proc.stdin.flush()
            except OSError:
                self.proc = None
                on_line("The administrator helper stopped; try the step again.")
                return 1
            for raw in iter(self.proc.stdout.readline, b""):
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if "line" in msg:
                    on_line(msg["line"])
                elif "exit" in msg:
                    return int(msg["exit"])
            self.proc = None
            on_line("The administrator helper stopped before the step finished.")
            return 1

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                pass
