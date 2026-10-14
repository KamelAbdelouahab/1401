"""Local diagnostic files survive restart and upload failure. Saving never sends a report. (DurableReport.cs)"""
import datetime
import getpass
import os
import re
import socket
import uuid

MAX_CHARACTERS = 500000
NAME = re.compile(r"\A\d{8}T\d{9}Z-[a-z][a-z0-9-]{0,31}-[0-9a-f]{32}\.txt\Z")
HARDWARE_VENDORS = {v.lower() for v in ("Apple", "Intel", "AMD", "NVIDIA", "Microsoft", "Realtek", "Broadcom", "Qualcomm",
                                         "MediaTek", "ASUS", "ASUSTeK", "ASRock", "Dell", "Lenovo", "HP", "Gigabyte", "MSI",
                                         "Acer", "AuthenticAMD", "GenuineIntel")}


class Outcome:
    def __init__(self, path=None, failure=None):
        self.path, self.failure = path, failure

    @property
    def saved(self):
        return self.path is not None


def _user():
    for get in (getpass.getuser, lambda: os.environ.get("USER", "")):
        try:
            v = get()
            if v:
                return v
        except Exception:  # noqa: BLE001
            pass
    return ""


def clean(value):
    return clean_identity(value, _user(), socket.gethostname(), os.path.expanduser("~"))


def clean_identity(value, user, host, home):
    """Explicit fields and home paths keep their privacy meaning even when an account shares a vendor word."""
    value = value or ""
    if home and len(home) > 3 and home not in ("/root",):
        value = re.sub(re.escape(home), "~", value, flags=re.IGNORECASE)
    value = re.sub(r"/home/[^/\r\n\"'\t ]+", "~", value)
    value = re.sub(r"(?i)[a-z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)[^\\/\r\n\"\t]+", "%USERPROFILE%", value)
    identities = [(user, "user", r"user(?:name)?|account|login"),
                  (host, "this-pc", r"host(?:name)?|computer(?: name)?|machine(?: name)?|pc")]
    for name, repl, fields in identities:
        if not name or len(name) > 256:
            continue
        esc = re.escape(name)
        pattern = r"(?i)(\b(?:" + fields + r")\b[\"']?\s*(?:[:=]\s*[\"']?|\s+))" + esc + r"(?![\w.-])"
        value = re.sub(pattern, lambda m, r=repl: m.group(1) + r, value)
        if repl == "this-pc":
            value = re.sub(r"(?i)(\\{2,4})" + esc + r"(?=\\)", lambda m, r=repl: m.group(1) + r, value)
            value = re.sub(r"(?i)(https?://)" + esc + r"(?=[/:?#]|$)", lambda m, r=repl: m.group(1) + r, value)
    for name, repl, _ in identities:
        if not name or len(name) <= 2 or len(name) > 256:
            continue
        esc = re.escape(name)
        current = value

        def sub(m, r=repl, text=None):
            if _hardware_literal(current, m):
                return m.group(0)
            return r
        value = re.sub(r"(?i)(?<!\w)" + esc + r"(?!\w)", sub, current)
    return value


def _hardware_literal(text, m):
    word = m.group(0)
    if word.lower() not in HARDWARE_VENDORS:
        return False
    lower = max(0, m.start() - 160)
    nl = text.rfind("\n", lower, m.start())
    start = max(lower, nl + 1)
    before = text[start:m.start()]
    after = text[m.end():m.end() + 96]
    if re.fullmatch(r"(?i)apple|nvidia|intel|microsoft|amd", word) and re.search(r"(?i)(?:^|[^a-z0-9_.-])com\.$", before) \
            and after.startswith("."):
        return True
    if word.lower() == "nvidia" and (after.lower().startswith("-macos-driver") or
                                     (before.lower().endswith("nullmoth-") and re.match(r"^-[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz", after))):
        return True
    if re.search(r"(?i)(?:[\"']?(?:manufacturer|vendor|board|motherboard|cpu|gpu|processor name)[\"']?\s*[:=]\s*[\"']?)$", before):
        return True
    return bool(re.match(r"(?i)^\s*(?:\(R\)|\(0x[0-9a-f]+\)|GeForce\b|Quadro\b|RTX\b|GTX\b|Ryzen\b|Radeon\b|Core\b|UHD\b|"
                         r"Iris\b|HD Graphics\b|Wi-Fi\b|Wireless\b|Ethernet\b)", after))


def _real_parents(directory):
    """The report folder and the app data folder must be real folders, never links aimed somewhere else."""
    for p in (directory, os.path.dirname(directory)):
        if os.path.islink(p):
            raise OSError("A diagnostic folder is a symbolic link.")


def save(work, kind, version, text):
    temporary = None
    try:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", kind or ""):
            raise ValueError("Invalid diagnostic stage.")
        directory = os.path.join(work, "diagnostic-reports")
        _real_parents(directory)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        _real_parents(directory)
        now = datetime.datetime.now(datetime.timezone.utc)
        stem = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}Z-" + kind + "-" + uuid.uuid4().hex
        path = os.path.join(directory, stem + ".txt")
        temporary = path + ".partial"
        raw = text or ""
        truncated = len(raw) > MAX_CHARACTERS
        body = clean(raw[:MAX_CHARACTERS] if truncated else raw)
        if truncated:
            body += "\n[report truncated at local diagnostic limit]\n"
        data = ("1401 " + clean(version) + " local " + kind + " report\n" + body).encode("utf-8")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _real_parents(directory)
        if os.path.lexists(path):
            raise FileExistsError(path)
        os.rename(temporary, path)
        temporary = None
        return Outcome(path=path)
    except Exception as error:  # noqa: BLE001
        return Outcome(failure="The local diagnostic report could not be saved (" + type(error).__name__ + "). Check free "
                       "space and access to the app data folder, then retry. No saved report is claimed.")
    finally:
        if temporary is not None:
            try:
                os.remove(temporary)
            except OSError:
                pass


def find(work):
    directory = os.path.join(work, "diagnostic-reports")
    _real_parents(directory)
    if not os.path.isdir(directory):
        return []
    names = [n for n in os.listdir(directory) if NAME.match(n) and not os.path.islink(os.path.join(directory, n))]
    return [os.path.join(directory, n) for n in sorted(names, reverse=True)[:12]]


def is_local(work, path):
    return os.path.dirname(os.path.abspath(path)) == os.path.abspath(os.path.join(work, "diagnostic-reports"))
