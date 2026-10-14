"""Runs the 1401 engine (the bundled Python + p1401 beside this app) and streams its output. (Engine.cs)

Engine steps run as the user, except Check this PC and writing the stick: those go through the helper that polkit
(pkexec) or sudo started as root (privileged.py)."""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import uuid

from . import VERSION, durable

HOME = os.environ.get("A1401_HOME") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON = sys.executable
APP_DIR = os.environ.get("A1401_ENGINE_APP") or os.path.join(HOME, "engine", "app")
# Never beside the app: an unpacked or installed folder may be read-only. The user's own data folder, as on Windows.
WORK = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"), "1401")
NULLMOTH_DIR = os.path.join(HOME, "NullMoth")
PRIVILEGED = ("p1401.scan ", "p1401.usbwriter ")

last_report = None


def product_version():
    return VERSION + " (Linux)"


def missing():
    if not os.path.isfile(PYTHON):
        return "The engine folder is missing next to 1401 (engine/python). Copy the whole 1401 folder, not just the launcher."
    if not os.path.isdir(os.path.join(APP_DIR, "p1401")):
        return "The engine folder is incomplete (engine/app/p1401)."
    return None


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def input_fingerprints():
    text = ["fingerprint scope: files observed before this attempt; not executed-memory identity"]
    for name, path in (("app", os.path.join(HOME, "app", "a1401", "mainwindow.py")),
                       ("build-manifest", os.path.join(HOME, "BUILD-MANIFEST.json")),
                       ("engine-source", os.path.join(APP_DIR, "p1401", "engine.py")),
                       ("upstream-pin", os.path.join(APP_DIR, "upstream", "PINNED.json")),
                       ("iasl", os.path.join(APP_DIR, "upstream", "OpCore-Simplify", "Scripts", "iasl"))):
        try:
            if not os.path.isfile(path) or os.path.islink(path) or os.path.getsize(path) > 32 * 1024 * 1024:
                text.append(name + ": unavailable")
                continue
            text.append(name + ": SHA256 " + _sha(path))
        except OSError as error:
            text.append(f"{name}: unavailable ({type(error).__name__})")
    try:
        manifest = os.path.join(HOME, "BUILD-MANIFEST.json")
        if os.path.isfile(manifest) and os.path.getsize(manifest) <= 2 * 1024 * 1024:
            with open(manifest, encoding="utf-8") as fh:
                value = json.load(fh)
            source = str(value.get("source_commit", ""))
            if re.fullmatch(r"[0-9a-f]{40}", source):
                text.append("manifest declared source commit: " + source)
            for key, digest in (value.get("files") or {}).items():
                if re.fullmatch(r"NullMoth/(nullmoth-nvidia-\d+\.\d+\.\d+\.tar\.gz|1401-Mac-\d+\.\d+\.\d+\.zip)", key) and \
                        re.fullmatch(r"[0-9a-f]{64}", str(digest)):
                    text.append(f"manifest declared companion pin {os.path.basename(key)}: {digest}")
    except Exception as error:  # noqa: BLE001
        text.append(f"manifest pins unavailable ({type(error).__name__})")
    return "\n".join(text) + "\n"


def stage_of(args):
    return ("scan" if args[0] == "p1401.scan" else "build" if args[:2] == ["p1401", "build"] else
            "write" if args[0] == "p1401.usbwriter" else "operation")


def run(args, on_line, helper=None):
    """Runs `python -m <args>` in the engine folder; every output line goes to on_line. Blocking: call it from a worker
    thread. args is a list. Saves a local report before and after, like the Windows app."""
    global last_report
    last_report = None
    attempt = uuid.uuid4().hex
    stage = stage_of(args)
    shown = " ".join(args)
    context = (f"app-reported distribution version: {product_version()}\nengine identity: engine-source SHA256 observation "
               f"below; no separate engine version is declared\nattempt: {attempt}\nstage: {stage}\noperation: {shown}\n")
    started = durable.save(WORK, stage + "-start", product_version(), context + "state: attempt recorded before execution\n")
    if not started.saved:
        last_report = started
        on_line(started.failure + " The operation was not started.")
        return 1
    output, lock, state = [], threading.Lock(), {"size": 0, "truncated": False}

    def record(line):
        with lock:
            left = durable.MAX_CHARACTERS - state["size"]
            if left > 0:
                piece = line[:left]
                output.append(piece)
                state["size"] += len(piece) + 1
            if len(line) > left:
                state["truncated"] = True
        try:
            on_line(line)
        except Exception as notification:  # noqa: BLE001
            with lock:
                output.append(f"Output notification failed ({type(notification).__name__}); local reporting continues.")
    code = 1
    try:
        context += input_fingerprints()
        privileged = any((shown + " ").startswith(p) for p in PRIVILEGED) and os.geteuid() != 0
        if privileged:
            if helper is None:
                raise RuntimeError("no administrator helper")
            code = helper.run(args, record)
        else:
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
            p = subprocess.Popen([PYTHON, "-u", "-m"] + list(args), cwd=APP_DIR, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for raw in iter(p.stdout.readline, b""):
                record(raw.decode("utf-8", "replace").rstrip("\r\n"))
            code = p.wait()
    except Exception as error:  # noqa: BLE001
        record(f"Engine operation failed ({type(error).__name__}: {error}). Check the complete extracted app folder and "
               "send the saved local report.")
    finally:
        with lock:
            saved = context + f"exit code: {code}\n" + "\n".join(output) + ("\n[output truncated]" if state["truncated"] else "")
        last_report = durable.save(WORK, stage + "-final", product_version(), saved)
    return code
