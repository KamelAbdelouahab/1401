"""The root half of 1401 for Linux. The window runs as the user; the two steps that need root - reading the firmware's
ACPI tables (Check this PC) and erasing the stick - run here, started once through polkit (pkexec) or sudo, the way
Windows asks for administrator rights once when 1401.exe opens.

Only those two engine commands are accepted. Requests come as JSON lines on stdin; each output line goes back as
{"line": ...} and the end as {"exit": code}. Files the root step leaves in the user's 1401 data folder are handed back
to the user, so the window can read and delete them later.

    pkexec <python> -I helper.py --app <engine/app> --work <~/.local/share/1401> --uid <uid> --gid <gid>
"""
import json
import os
import subprocess
import sys

ALLOWED = {("p1401.scan",): "scan", ("p1401.usbwriter", "write"): "write"}
ENV_PASS = ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE",
            "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "P1401_TEST_LOOP_AS_USB")


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def allowed(argv):
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and "\0" not in a for a in argv):
        return False
    return (argv[0],) in ALLOWED or tuple(argv[:2]) in ALLOWED


def give_back(work, uid, gid):
    """lchown everything under the user's 1401 folder that root created, never following a link out of it."""
    if uid is None or not os.path.isdir(work) or os.path.islink(work):
        return
    for top, dirs, files in os.walk(work, followlinks=False):
        for name in dirs + files:
            p = os.path.join(top, name)
            try:
                st = os.lstat(p)
                if st.st_uid == 0:
                    os.lchown(p, uid, gid)
            except OSError:
                pass


def main(argv):
    def opt(name):
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None
    app, work = opt("--app"), opt("--work")
    uid = os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID") or opt("--uid")
    gid = os.environ.get("SUDO_GID") or opt("--gid")
    uid = int(uid) if uid and uid.isdigit() else None
    gid = int(gid) if gid and gid.isdigit() else (uid if uid is not None else None)
    if not app or not os.path.isdir(os.path.join(app, "p1401")) or not work:
        send({"error": "helper started without the engine folder"})
        return 2
    send({"ready": True, "euid": os.geteuid()})
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw.startswith("{"):
            continue  # a sudo password line that sudo did not need
        try:
            req = json.loads(raw)
        except ValueError:
            continue
        args = req.get("argv")
        if not allowed(args):
            send({"line": "1401: this step does not run with administrator rights."})
            send({"exit": 1})
            continue
        env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8",
               "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "HOME": os.environ.get("HOME", "/root")}
        for k, v in (req.get("env") or {}).items():
            if k in ENV_PASS and isinstance(v, str):
                env[k] = v
        try:
            p = subprocess.Popen([sys.executable, "-u", "-m"] + args, cwd=app, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for line in iter(p.stdout.readline, b""):
                send({"line": line.decode("utf-8", "replace").rstrip("\r\n")})
            rc = p.wait()
        except Exception as e:  # noqa: BLE001
            send({"line": f"The step could not start ({type(e).__name__})."})
            rc = 1
        give_back(work, uid, gid)
        send({"exit": rc})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
