"""Prepare, check and package 1401 for Linux from pinned release inputs (linux/release-inputs.json).

    python3 tools/linux_release.py prepare --stage <empty dir> --cache <dir>
    python3 tools/linux_release.py check   --stage <dir> --output <evidence.json>     (needs a display or xvfb-run)
    python3 tools/linux_release.py package --stage <dir> --evidence <evidence.json> --output <dir>

The folder it makes is the Linux counterpart of the Windows zip:

    1401/
      1401                  the launcher (double-click or ./1401)
      install.sh            adds 1401 to the application menu for this user
      app/a1401/            the window (Python + Qt 6)                               [linux/app/]
      engine/python/        Python 3.12 (python-build-standalone) with PySide6 and certifi, pruned to what 1401 uses
      engine/app/p1401/     the engine                                                 [p1401/]
      engine/app/upstream/  OpCore-Simplify and Hardware Sniffer (pinned), Scripts/iasl for Linux (pinned)
      NullMoth/             the NVIDIA driver package and the 1401 Mac app (copied to the stick)
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

REPO = Path(__file__).resolve().parents[1]
INPUTS = REPO / "linux" / "release-inputs.json"
PYV = "3.12"
QT_MODULES = ("QtCore", "QtGui", "QtWidgets", "QtDBus")
QT_PLUGINS = {
    "platforms": ("libqxcb.so", "libqwayland-generic.so", "libqwayland-egl.so", "libqoffscreen.so", "libqminimal.so"),
    "wayland-shell-integration": None, "wayland-decoration-client": None, "wayland-graphics-integration-client": None,
    "xcbglintegrations": None,
    "platforminputcontexts": ("libcomposeplatforminputcontextplugin.so", "libibusplatforminputcontextplugin.so"),
    "imageformats": ("libqjpeg.so", "libqgif.so", "libqico.so"),
    "platformthemes": ("libqxdgdesktopportal.so",),
}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url, digest, dest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        raise RuntimeError("A verified SHA-256 is required for every release input.")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and sha(dest) == digest:
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "1401-linux-release"})
        with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as out:
            shutil.copyfileobj(r, out, 1 << 20)
        if sha(part) != digest:
            raise RuntimeError("Release input checksum mismatch: " + dest.name)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return dest


def version():
    v = json.loads(INPUTS.read_text())["version"]
    sys.path.insert(0, str(REPO / "linux" / "app"))
    from a1401 import VERSION  # noqa: PLC0415
    if v != VERSION or not re.fullmatch(r"\d+\.\d+\.\d+", v):
        raise RuntimeError("linux/release-inputs.json and a1401.VERSION differ.")
    return v


def needed(path):
    """DT_NEEDED of an ELF file (readelf, from binutils)."""
    out = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True).stdout
    return re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", out)


def extract_deb_libs(deb, dest):
    """The .so files of a .deb (ar archive -> data.tar.*), without dpkg."""
    data = Path(deb).read_bytes()
    if data[:8] != b"!<arch>\n":
        raise RuntimeError("not a .deb: " + Path(deb).name)
    pos, member = 8, None
    while pos < len(data):
        name = data[pos:pos + 16].decode().strip().rstrip("/")
        size = int(data[pos + 48:pos + 58].decode().strip())
        body = data[pos + 60:pos + 60 + size]
        if name.startswith("data.tar"):
            member = (name, body)
        pos += 60 + size + (size & 1)
    if not member:
        raise RuntimeError("no data archive in " + Path(deb).name)
    name, body = member
    if name.endswith(".zst"):
        body = subprocess.run(["zstd", "-dc"], input=body, capture_output=True, check=True).stdout
        mode = "r:"
    else:
        mode = "r:xz" if name.endswith(".xz") else "r:gz"
    got = []
    with tarfile.open(fileobj=io.BytesIO(body), mode=mode) as t:
        for m in t.getmembers():
            base = os.path.basename(m.name)
            if m.isfile() and ".so." in base and m.name.startswith("./usr/lib/"):
                (Path(dest) / base).write_bytes(t.extractfile(m).read())
                got.append(base)
            elif m.issym() and ".so." in base and m.name.startswith("./usr/lib/"):
                link = Path(dest) / base
                if not link.exists():
                    link.symlink_to(os.path.basename(m.linkname))
                got.append(base)
    return got


def prune_python(py):
    lib = py / "lib"
    std = lib / ("python" + PYV)
    for rel in ("test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data", "turtle.py"):
        p = std / rel
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    for p in list(std.glob("lib-dynload/_tkinter*")) + list(lib.glob("libtcl*")) + list(lib.glob("libtk*")):
        p.unlink()
    for pattern in ("tcl*", "tk*", "itcl*", "thread*", "pkgconfig"):
        for p in lib.glob(pattern):
            if p.is_dir():
                shutil.rmtree(p)
    for d in ("include", "share"):
        shutil.rmtree(py / d, ignore_errors=True)
    for p in (py / "bin").iterdir():
        if re.match(r"(idle|pydoc|2to3|pip)", p.name):
            p.unlink()
    site = std / "site-packages"
    for p in site.glob("pip*"):
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    for p in std.rglob("__pycache__"):
        if "site-packages" in p.parts:
            shutil.rmtree(p, ignore_errors=True)


def prune_qt(site):
    pyside = site / "PySide6"
    qt = pyside / "Qt"
    for p in list(pyside.iterdir()):
        keep = (p.name in ("Qt", "__init__.py", "_config.py", "_git_pyside_version.py", "support", "__feature__.pyi") or
                p.name.startswith("libpyside6.abi3.so") or
                any(p.name.startswith(m + ".") for m in QT_MODULES))
        if not keep:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    plugins = qt / "plugins"
    for d in list(plugins.iterdir()):
        want = QT_PLUGINS.get(d.name, False)
        if want is False:
            shutil.rmtree(d)
        elif want:
            for f in d.iterdir():
                if f.name not in want:
                    f.unlink()
    for d in list(qt.iterdir()):
        if d.name not in ("lib", "plugins"):
            shutil.rmtree(d) if d.is_dir() else d.unlink()
    # keep only the Qt libraries the kept modules and plugins load, followed through their DT_NEEDED
    libdir = qt / "lib"
    present = {p.name for p in libdir.iterdir()}
    todo = [p for p in pyside.glob("Qt*.abi3.so")] + [p for p in plugins.rglob("*.so")]
    todo += list(site.glob("shiboken6/*.so*"))
    keep = set()
    while todo:
        f = todo.pop()
        for n in needed(f):
            if n in present and n not in keep:
                keep.add(n)
                todo.append(libdir / n)
    for p in libdir.iterdir():
        if p.name not in keep and not p.name.startswith(("libxcb-", "libxkbcommon-x11")):
            shutil.rmtree(p) if p.is_dir() else p.unlink()


def strip(paths):
    if shutil.which("strip"):
        for p in paths:
            subprocess.run(["strip", "--strip-unneeded", str(p)], capture_output=True)


def prepare(args):
    inputs = json.loads(INPUTS.read_text())
    win = json.loads((REPO / inputs["companion_inputs"]).read_text())
    ver = version()
    stage = Path(args.stage).resolve()
    if stage.exists() and any(stage.iterdir()):
        raise RuntimeError("The staging directory must be empty.")
    cache = Path(args.cache).resolve()
    root = stage / "1401"
    (root / "engine").mkdir(parents=True)

    # Python
    pyarchive = download(inputs["python"]["url"], inputs["python"]["sha256"], cache / "python.tar.gz")
    with tarfile.open(pyarchive) as t:
        t.extractall(root / "engine", filter="tar")
    py = root / "engine" / "python"
    python = py / "bin" / "python3"
    site = py / "lib" / ("python" + PYV) / "site-packages"
    wheels = cache / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    for w in inputs["wheels"]:
        target = wheels / w["name"]
        if not (target.exists() and sha(target) == w["sha256"]):
            subprocess.run([str(python), "-m", "pip", "download", "--no-deps", "--only-binary=:all:", "--python-version", PYV,
                            "--platform", "manylinux_2_28_x86_64", "-d", str(wheels), w["pypi"]], check=True)
        if sha(target) != w["sha256"]:
            raise RuntimeError("Wheel checksum mismatch: " + w["name"])
        with zipfile.ZipFile(target) as z:
            z.extractall(site)
    for p in site.rglob("*"):
        if p.is_file() and (p.suffix == ".so" or ".so." in p.name):
            p.chmod(0o755)
    prune_python(py)
    prune_qt(site)
    # X11 fallback libraries: Qt's xcb platform needs xcb-util pieces that GTK desktops do not always install
    debs = cache / "debs"
    for d in inputs["x11_fallback_libraries"]:
        deb = download(d["url"], d["sha256"], debs / d["url"].rsplit("/", 1)[1])
        extract_deb_libs(deb, site / "PySide6" / "Qt" / "lib")
    strip([py / "lib" / ("libpython" + PYV + ".so.1.0")])

    # Engine
    app = root / "engine" / "app"
    shutil.copytree(REPO / "p1401", app / "p1401", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if not inputs.get("branded"):
        (app / "p1401" / "moth-mark.jpg").unlink()
    tracked = subprocess.check_output(["git", "-C", str(REPO), "ls-files", "-z", "upstream"], text=True).split("\0")
    for rel in filter(None, tracked):
        if rel.endswith(".exe") or rel.endswith(".bat") or rel.endswith(".command"):
            continue
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, target)
        if rel.endswith("macserial.linux"):
            target.chmod(0o755)
    izip = download(inputs["iasl"]["url"], inputs["iasl"]["sha256"], cache / "iasl.zip")
    with zipfile.ZipFile(izip) as z:
        data = z.read("iasl")
    iasl = app / "upstream" / "OpCore-Simplify" / "Scripts" / "iasl"
    iasl.write_bytes(data)
    iasl.chmod(0o755)

    # Window, launcher, menu entry
    shutil.copytree(REPO / "linux" / "app" / "a1401", root / "app" / "a1401", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    share = REPO / "linux" / "share"
    for name in ("1401", "install.sh"):
        shutil.copyfile(share / name, root / name)
        (root / name).chmod(0o755)
    (root / "share" / "applications").mkdir(parents=True)
    (root / "share" / "icons").mkdir(parents=True)
    shutil.copyfile(share / "1401.desktop", root / "share" / "applications" / "1401.desktop")
    shutil.copyfile(share / "1401.png", root / "share" / "icons" / "1401.png")
    if inputs.get("branded"):
        (root / "BRANDING-NULLMOTH").write_text("NullMoth Systems build\n")
        shutil.copyfile(REPO / "windows" / "App" / "moth-mark.jpg", root / "app" / "a1401" / "moth-mark.jpg")
    for name in ("LICENSE", "NOTICE.md"):
        shutil.copyfile(REPO / name, root / name)

    # NullMoth companion payload: the same pins as the Windows package
    package = runpy.run_path(str(REPO / "p1401" / "nullmoth.py"))["PACKAGE"]
    bundle = root / "NullMoth"
    download(package["url"], package["sha256"], cache / package["name"])
    mac_url = package["url"].rsplit("/", 1)[0] + "/" + win["mac_asset"]
    download(mac_url, win["mac_sha256"], cache / win["mac_asset"])
    bundle.mkdir()
    shutil.copyfile(cache / package["name"], bundle / package["name"])
    shutil.copyfile(cache / win["mac_asset"], bundle / win["mac_asset"])

    (root / "README.txt").write_text(
        f"1401 {ver} for Linux (x86_64; tested on Debian 13 and CachyOS).\n"
        "Keep the entire 1401 folder together.\n"
        "Run ./1401 (or double-click it) to check the PC and build its macOS installation setup.\n"
        "./install.sh adds 1401 to your application menu.\n"
        "Reading the ACPI tables and erasing the stick ask for your password (polkit or sudo).\n"
        "Hardware support depends on the detected devices; this package does not qualify every machine.\n"
        + ("" if inputs.get("branded") else
           "This is a modified build of 1401 (PolyForm Noncommercial 1.0.0); it is not made or endorsed by NullMoth Systems.\n"))
    print("Linux candidate prepared at", root)


def check(args):
    """Runs the packaged app and engine the way a user would, and records what was checked."""
    root = Path(args.stage).resolve() / "1401"
    python = root / "engine" / "python" / "bin" / "python3"
    env = dict(os.environ, A1401_HOME=str(root), PYTHONPATH=str(root / "app"), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop("A1401_ENGINE_APP", None)
    evidence = {"version": version(), "checks": {}}
    checks = evidence["checks"]

    def step(name, cmd, cwd=None, extra=None, ok=lambda p: p.returncode == 0):
        p = subprocess.run(cmd, cwd=cwd, env=dict(env, **(extra or {})), capture_output=True, text=True, timeout=900)
        passed = ok(p)
        checks[name] = {"ok": bool(passed), "tail": (p.stdout + p.stderr)[-1500:]}
        print(("ok   " if passed else "FAIL ") + name)
        if not passed:
            print("     " + (p.stdout + p.stderr)[-3000:].replace("\n", "\n     "))
        return p

    with tempfile.TemporaryDirectory() as tmp:
        step("runtime imports", [str(python), "-c", "import PySide6.QtWidgets, certifi, ssl, sqlite3, zlib, lzma, ctypes; "
                                 "print(ssl.OPENSSL_VERSION)"])
        result = Path(tmp) / "verify.json"
        p = step("window --verify-package", [str(root / "1401"), "--verify-package", str(result)],
                 extra={"XDG_DATA_HOME": tmp})
        try:
            checks["window --verify-package"]["result"] = json.loads(result.read_text())
            checks["window --verify-package"]["ok"] &= checks["window --verify-package"]["result"].get("ok") is True
        except (OSError, ValueError):
            checks["window --verify-package"]["ok"] = False
        if not checks["window --verify-package"]["ok"]:
            print("     result:", checks["window --verify-package"].get("result"))
        app = root / "engine" / "app"
        step("usbwriter selftest", [str(python), "-m", "p1401.usbwriter", "--selftest"], cwd=app)
        img = Path(tmp) / "fat.img"
        with open(img, "wb") as fh:
            fh.truncate(2 * (1 << 30))
        step("FAT32 formatter", [str(python), "-m", "p1401.fat32", str(img)], cwd=app)
        if shutil.which("fsck.fat"):
            step("FAT32 passes fsck.fat", ["fsck.fat", "-n", str(img)])
        step("iasl runs", [str(app / "upstream" / "OpCore-Simplify" / "Scripts" / "iasl"), "-v"],
             ok=lambda p: "ACPI" in p.stdout + p.stderr)
        step("macserial runs", [str(app / "upstream" / "OpCore-Simplify" / "Scripts" / "macserial.linux"), "-g", "-m", "iMac19,1", "-n", "1"],
             ok=lambda p: p.returncode == 0 and "|" in p.stdout)
        step("guide renders for Linux", [str(python), "-c",
             "from p1401 import guide; r={'Motherboard':{'Name':'X','Platform':'Desktop'},'CPU':{'Manufacturer':'Intel'},'BIOS':{}}; "
             "h=guide.render_html(r, guide.build_guide(r, {}, host='linux'), brand=False); assert 'bcdedit' not in h; print(len(h))"], cwd=app)
        corpus = REPO / "tests" / "corpus" / "cache" / "optiplex-5050-micro"
        if corpus.is_dir():
            step("engine plans a real report", [str(python), "-m", "p1401", "plan", str(corpus / "Report.json"), str(corpus / "ACPI"), "--json"],
                 cwd=app, ok=lambda p: p.returncode == 0 and '"ok": true' in p.stdout)
    bytecode = [str(p.relative_to(root)) for p in root.rglob("__pycache__") if "engine/python" not in str(p)]
    checks["no generated bytecode outside the runtime"] = {"ok": not bytecode, "tail": "\n".join(bytecode)}
    evidence["ok"] = all(c["ok"] for c in checks.values())
    evidence["candidate_files"] = {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*"))
                                   if p.is_file() and not p.is_symlink()}
    Path(args.output).write_text(json.dumps(evidence, indent=2) + "\n")
    print("packaged verification", "passed" if evidence["ok"] else "FAILED")
    return 0 if evidence["ok"] else 1


def package(args):
    root = Path(args.stage).resolve() / "1401"
    evidence = json.loads(Path(args.evidence).read_text())
    ver = version()
    if evidence.get("ok") is not True or evidence.get("version") != ver:
        raise RuntimeError("Successful verification of this packaged version is required.")
    actual = {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}
    if actual != evidence.get("candidate_files"):
        raise RuntimeError("The candidate files changed after packaged verification.")
    manifest = root / "BUILD-MANIFEST.json"
    commit = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    manifest.write_text(json.dumps({"version": ver, "platform": "linux-x86_64", "source_commit": commit,
                                    "release_inputs": json.loads(INPUTS.read_text()), "verification_sha256": sha(args.evidence),
                                    "files": actual}, indent=2, sort_keys=True) + "\n")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f"1401-Linux-{ver}-x86_64.tar.gz"

    def norm(info):
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mtime = 315532800  # 1980-01-01, like the Windows zip
        if info.isfile():
            info.mode = 0o755 if (info.mode & 0o111) else 0o644
        elif info.isdir():
            info.mode = 0o755
        return info
    import gzip  # noqa: PLC0415
    with open(archive, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        entries = sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix())
        tar.add(root, arcname="1401", recursive=False, filter=norm)
        for p in entries:
            tar.add(p, arcname="1401/" + p.relative_to(root).as_posix(), recursive=False, filter=norm)
    (out / "SHA256SUMS.txt").write_text(sha(archive) + "  " + archive.name + "\n")
    shutil.copyfile(args.evidence, out / "linux-verification.json")
    print("Verified Linux archive created:", archive)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--stage", required=True)
    p.add_argument("--cache", required=True)
    c = sub.add_parser("check")
    c.add_argument("--stage", required=True)
    c.add_argument("--output", required=True)
    k = sub.add_parser("package")
    k.add_argument("--stage", required=True)
    k.add_argument("--evidence", required=True)
    k.add_argument("--output", required=True)
    a = ap.parse_args()
    return {"prepare": prepare, "check": check, "package": package}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
