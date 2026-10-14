"""The Linux side of the stick writer: find USB disks, prove one is still the disk the user picked, erase it into one
active FAT32 partition on an MBR (the layout the Windows writer makes), mount it, and give it back.

Same rules as on Windows, enforced here and not in the window:
  - USB only (lsblk TRAN=usb). Never a disk holding the running system (/, /boot, /boot/efi, /usr, /home, /var,
    swap in use - also through LVM, LUKS or RAID), never a read-only disk, nothing under 2 GiB.
  - The disk is read again right before erasing; model, serial, size and bus must match what the user picked.
  - A disk carrying an installed OS (an EFI system, APFS, HFS+, Microsoft reserved or Windows recovery partition on GPT)
    is refused.
  - Every volume on the stick is unmounted first. The raw disk is then opened with O_EXCL, which the kernel refuses
    while anything still mounts or holds it: the Linux form of the volume lock Rufus takes on Windows.

Everything runs as root (the window asks for the password through polkit). mkfs.fat is used when dosfstools is
installed; otherwise p1401.fat32 formats the partition. Files go on through the kernel's vfat driver.
"""
import errno
import fcntl
import json
import os
import shutil
import stat
import struct
import subprocess
import tempfile
import time

GiB = 1 << 30
MiB = 1 << 20
FAT32_LBA = 0x0C
BLKRRPART = 0x125F
BLKSSZGET = 0x1268
BLKGETSIZE64 = 0x80081272
SYSTEM_MOUNTS = ("/", "/boot", "/boot/efi", "/efi", "/usr", "/home", "/var", "/opt", "/srv", "[SWAP]")
# GPT types of an installed OS (same set as the Windows writer): EFI system, Apple APFS, Apple HFS+, Microsoft
# reserved, Windows recovery
OS_TYPES = {"c12a7328-f81f-11d2-ba4b-00a0c93ec93b": "an EFI system partition",
            "7c3457ef-0000-11aa-aa11-00306543ecac": "an APFS (macOS) partition",
            "48465300-0000-11aa-aa11-00306543ecac": "an HFS+ (macOS) partition",
            "e3c9e316-0b5c-4db8-817d-f92df00215ae": "a Microsoft reserved partition",
            "de94bba4-06d1-4d40-a16a-bfd50179d6ac": "a Windows recovery partition"}
COLUMNS = "NAME,PATH,TYPE,SIZE,TRAN,RM,HOTPLUG,RO,MODEL,VENDOR,SERIAL,PTTYPE,PARTTYPE,FSTYPE,LABEL,MOUNTPOINTS"
TIMEOUT = 120
# Test harnesses attach a file-backed loop device in place of a stick. Off unless the variable is set; a loop device
# is a file, never a disk with someone's data on it.
TEST_LOOP = os.environ.get("P1401_TEST_LOOP_AS_USB") == "1"


class DiskError(RuntimeError):
    pass


def _run(args, timeout=TIMEOUT, check=True):
    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        raise DiskError(f"{args[0]} is not installed (it comes with util-linux)") from None
    except subprocess.TimeoutExpired:
        raise DiskError(f"{args[0]} did not finish in {timeout} s") from None
    if check and p.returncode != 0:
        raise DiskError(f"{' '.join(args[:2])} failed: " + (p.stderr or p.stdout).strip()[-800:])
    return p


def lsblk(path=None):
    """lsblk's JSON tree (disks with their partitions and anything stacked on them)."""
    cols = COLUMNS
    args = ["lsblk", "-J", "-b", "-o", cols] + ([path] if path else [])
    p = _run(args, check=False)
    if p.returncode != 0 and "MOUNTPOINTS" in (p.stderr or ""):
        # util-linux before 2.37 has only MOUNTPOINT
        p = _run(["lsblk", "-J", "-b", "-o", cols.replace("MOUNTPOINTS", "MOUNTPOINT")] + ([path] if path else []))
    elif p.returncode != 0:
        raise DiskError("lsblk failed: " + (p.stderr or p.stdout).strip()[-800:])
    return json.loads(p.stdout or "{}").get("blockdevices") or []


def _mounts(node):
    out = [m for m in (node.get("mountpoints") or [node.get("mountpoint")]) if m]
    for child in node.get("children") or []:
        out += _mounts(child)
    return out


def _stacked(node):
    """Device-mapper, RAID or loop users on top of this disk's partitions (an open LUKS volume, LVM, mdadm)."""
    out = []
    for child in node.get("children") or []:
        if child.get("type") not in ("part",):
            out.append(f"{child.get('name')} ({child.get('type')})")
        out += _stacked(child)
    return out


def _flag(v):
    return v in (True, 1, "1", "true")


def describe(node):
    """One lsblk disk node -> the dict shape the writer and the window share with the Windows backend."""
    mounts = _mounts(node)
    system = sorted({m for m in mounts if m in SYSTEM_MOUNTS or m.startswith("/boot")})
    tran = (node.get("tran") or "").lower()
    bus = "USB" if tran == "usb" or (TEST_LOOP and node.get("type") == "loop") else (tran.upper() or node.get("type", "?"))
    name = " ".join(x for x in ((node.get("vendor") or "").strip(), (node.get("model") or "").strip()) if x)
    parts = []
    for child in node.get("children") or []:
        if child.get("type") == "part":
            parts.append({"path": child.get("path"), "parttype": (child.get("parttype") or "").lower(),
                          "fstype": child.get("fstype"), "label": child.get("label"), "mountpoints": _mounts(child)})
    return {
        "Backend": "linux",
        "Number": node.get("name"),
        "Path": node.get("path") or "/dev/" + node.get("name", ""),
        "FriendlyName": name or node.get("name"),
        "SerialNumber": (node.get("serial") or "").strip(),
        "BusType": bus,
        "Size": int(node.get("size") or 0),
        "IsBoot": bool(system),
        "IsSystem": bool(system),
        "SystemMounts": system,
        "IsOffline": False,
        "IsReadOnly": _flag(node.get("ro")),
        "Removable": _flag(node.get("rm")) or _flag(node.get("hotplug")),
        "PartitionStyle": (node.get("pttype") or "RAW").upper(),
        "Partitions": parts,
        "Mountpoints": mounts,
        "Stacked": _stacked(node),
    }


def list_disks():
    out = []
    for node in lsblk():
        if node.get("type") == "disk" or (TEST_LOOP and node.get("type") == "loop"):
            out.append(describe(node))
    return out


def find(ident):
    """The disk the user picked, by kernel name (sdb) or path (/dev/sdb)."""
    ident = str(ident)
    for d in list_disks():
        if ident in (d["Number"], d["Path"]):
            return d
    return None


def os_partitions(disk):
    return [f"{p['path']} is {OS_TYPES[p['parttype']]}" for p in disk.get("Partitions", []) if p["parttype"] in OS_TYPES]


def recheck(picked):
    """Reads the disk again; raises unless it is still exactly the stick the user picked and safe to erase."""
    now = find(picked["Path"])
    if not now:
        raise DiskError(f"{picked['Path']} is not attached any more. Nothing was erased.")
    for key in ("SerialNumber", "Size", "FriendlyName", "BusType"):
        if now.get(key) != picked.get(key):
            raise DiskError(f"{picked['Path']} is not the USB stick you picked any more ({key} changed). Nothing was erased.")
    if now["BusType"] != "USB" or now["IsBoot"] or now["IsSystem"]:
        raise DiskError(f"{picked['Path']} is not a removable USB stick this PC can spare. Nothing was erased.")
    if now["IsReadOnly"]:
        raise DiskError(f"{picked['Path']} is read-only (a lock switch, or a failing stick). Nothing was erased.")
    found = os_partitions(now)
    if found:
        raise DiskError(f"{picked['Path']} holds an operating system ({'; '.join(found)}). Nothing was erased.")
    if now["Stacked"]:
        raise DiskError(f"{picked['Path']} is in use by {', '.join(now['Stacked'])} (an open encrypted volume, LVM or "
                        "RAID). Close it first. Nothing was erased.")
    return now


def unmount_all(disk, sleep=time.sleep, say=print):
    """Unmounts every volume on the stick. A busy volume gets retried, then lazily detached: the stick is about to be
    erased, so nothing still open on it is kept anyway (Rufus forces the dismount on Windows for the same reason)."""
    mounts = sorted(set(disk.get("Mountpoints") or []), key=len, reverse=True)
    for m in mounts:
        if m == "[SWAP]":
            raise DiskError("the stick is in use as swap space. Nothing was erased.")
        for _ in range(20):
            if _run(["umount", m], check=False).returncode == 0:
                break
            sleep(0.25)
        else:
            p = _run(["umount", "-l", m], check=False)
            if p.returncode != 0:
                raise DiskError(f"could not unmount {m}: {(p.stderr or '').strip()}. Close windows on the stick and try again.")
    if mounts:
        say(f"OK unmounted {len(mounts)} volume(s) on the stick")


def open_exclusive(path, sleep=time.sleep):
    """O_EXCL on a block device fails with EBUSY while it is mounted or held - the lock the erase needs."""
    for i in range(40):
        try:
            return os.open(path, os.O_RDWR | os.O_EXCL | getattr(os, "O_CLOEXEC", 0))
        except OSError as e:
            if e.errno != errno.EBUSY or i == 39:
                if e.errno == errno.EBUSY:
                    raise DiskError("another program is using the stick; close file manager windows on it and try "
                                    "again. Nothing was erased.") from None
                raise DiskError(f"cannot open {path}: {e.strerror}") from None
            sleep(0.25)


def sector_size(fd):
    try:
        buf = fcntl.ioctl(fd, BLKSSZGET, b"\0" * 4)
        return struct.unpack("<i", buf)[0] or 512
    except OSError:
        return 512


def device_size(fd):
    try:
        return struct.unpack("<Q", fcntl.ioctl(fd, BLKGETSIZE64, b"\0" * 8))[0]
    except OSError:
        return os.lseek(fd, 0, os.SEEK_END)


def mbr(size, part_size, sector, signature=None):
    """One active FAT32 (LBA) partition at 1 MiB, of at most part_size bytes and never past the disk's last MiB."""
    start = MiB // sector
    length = min(part_size, (size // MiB - 2) * MiB) // sector
    if length <= 0:
        raise DiskError("the stick is too small for a partition")
    sig = signature if signature is not None else (struct.unpack("<I", os.urandom(4))[0] or 1)
    b = bytearray(512)
    struct.pack_into("<I", b, 440, sig)
    # status 0x80 (active), CHS start/end set to the LBA-only marker values, type 0x0C, LBA start, sector count
    entry = struct.pack("<B3sB3sII", 0x80, b"\xFE\xFF\xFF", FAT32_LBA, b"\xFE\xFF\xFF", start, length)
    b[446:462] = entry
    b[510:512] = b"\x55\xAA"
    return bytes(b), start, length


def _write_at(fd, offset, data):
    os.lseek(fd, offset, os.SEEK_SET)
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def partition_path(disk_path, n=1):
    base = os.path.basename(disk_path)
    return disk_path + ("p" if base[-1:].isdigit() else "") + str(n)


def _known(part):
    return os.path.exists(os.path.join("/sys/class/block", os.path.basename(part)))


def reread(fd, path, sleep=time.sleep):
    """Asks the kernel to read the new table through our own handle (BLKRRPART)."""
    for _ in range(10):
        try:
            fcntl.ioctl(fd, BLKRRPART)
            return True
        except OSError as e:
            if e.errno != errno.EBUSY:
                return False
            sleep(0.5)
    return False


def reread_again(path, part, sleep=time.sleep):
    """After the handle is closed: if the kernel still has not listed the partition (it can skip the re-read of a
    disk opened exclusively, and some USB bridges ignore it), blockdev and partx ask again."""
    for _ in range(8):
        if _known(part):
            return True
        sleep(0.25)
    for args in (["blockdev", "--rereadpt", path], ["partx", "-u", path], ["partx", "-a", path]):
        try:
            subprocess.run(args, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for _ in range(8):
            if _known(part):
                return True
            sleep(0.25)
    return _known(part)


def wait_for(path, seconds=20, sleep=time.sleep):
    if shutil.which("udevadm"):
        subprocess.run(["udevadm", "settle", "--timeout=10"], capture_output=True)
    for _ in range(int(seconds * 4)):
        try:
            if stat.S_ISBLK(os.stat(path).st_mode):
                return True
        except OSError:
            pass
        sleep(0.25)
    return False


def erase(disk, part_size, say=print, sleep=time.sleep):
    """Unmount, lock, zero both ends, write the MBR, re-read it. Returns the new partition's path."""
    unmount_all(disk, sleep=sleep, say=say)
    path = disk["Path"]
    fd = open_exclusive(path, sleep=sleep)
    try:
        size = device_size(fd)
        if size != int(disk["Size"]):
            raise DiskError(f"{path} reports {size} bytes, not the {disk['Size']} you picked. Nothing was erased.")
        sector = sector_size(fd)
        table, start, length = mbr(size, part_size, sector)
        zero = bytes(MiB)
        # The partition table, the old GPT backup at the end, and the first MiB of the new partition (an old file
        # system there would get auto-mounted the moment the kernel sees the new table).
        for off in (0, start * sector, (size // MiB - 1) * MiB):
            _write_at(fd, off, zero)
        _write_at(fd, 0, table)
        os.fsync(fd)
        say("OK erased the stick and made one FAT32 partition")
        reread(fd, path, sleep=sleep)
    finally:
        os.close(fd)
    part = partition_path(path)
    reread_again(path, part, sleep=sleep)
    if not wait_for(part, sleep=sleep):
        raise DiskError(f"the new partition {part} did not appear. Unplug the stick, plug it back in and try again.")
    return part, start, length, sector


def _mounted_at(dev):
    real = os.path.realpath(dev)
    out = []
    try:
        with open("/proc/self/mounts", encoding="utf-8") as fh:
            for line in fh:
                f = line.split()
                if len(f) > 1 and os.path.realpath(f[0]) == real:
                    out.append(f[1].replace("\\040", " "))
    except OSError:
        pass
    return out


def mkfs(part, label, hidden, sector, say=print):
    # A desktop can auto-mount the new partition between the re-read and the format; let it go first.
    for m in _mounted_at(part):
        _run(["umount", m], check=False)
    tool = shutil.which("mkfs.fat") or shutil.which("mkfs.vfat")
    if tool:
        _run([tool, "-F", "32", "-n", label, "-h", str(hidden), "-S", str(sector), part])
        say("OK formatted FAT32 (mkfs.fat)")
    else:
        from . import fat32  # noqa: PLC0415
        fat32.format_volume(part, label=label, hidden=hidden, sector=sector)
        say("OK formatted FAT32 (built in; dosfstools is not installed)")


def mount(part, sleep=time.sleep):
    """Mounts the new FAT32 partition on a private directory. quiet: vfat accepts the chmod a copy makes."""
    target = tempfile.mkdtemp(prefix="1401-stick-", dir="/run" if os.access("/run", os.W_OK) else None)
    os.chmod(target, 0o755)
    last = ""
    for _ in range(10):
        p = _run(["mount", "-t", "vfat", "-o", "utf8,shortname=mixed,quiet,noatime", part, target], check=False)
        if p.returncode == 0:
            return target
        last = (p.stderr or p.stdout).strip()
        sleep(0.5)
    os.rmdir(target)
    raise DiskError(f"the stick was formatted but could not be mounted: {last}")


def unmount(target, say=print):
    say("STEP writing everything to the stick (this can take a few minutes on a slow stick)")
    os.sync()
    p = _run(["umount", target], check=False)
    if p.returncode != 0:
        _run(["sync"], check=False)
        time.sleep(2)
        p = _run(["umount", target], check=False)
    if p.returncode != 0:
        raise DiskError(f"the stick is written but could not be unmounted ({(p.stderr or '').strip()}); eject it from "
                        "the file manager before unplugging")
    try:
        os.rmdir(target)
    except OSError:
        pass
    say("OK all data is on the stick; it is safe to unplug")


def removable_mounts():
    """Mounted volumes on USB/removable disks, for the window (stick logs, driver update). [(mountpoint, disk name)]"""
    out = []
    try:
        nodes = lsblk()
    except DiskError:
        return out
    for node in nodes:
        if node.get("type") != "disk":
            continue
        d = describe(node)
        if not (d["BusType"] == "USB" or d["Removable"]) or d["IsSystem"]:
            continue
        for m in d["Mountpoints"]:
            if m and m != "[SWAP]" and os.path.isdir(m):
                out.append((m, d["Number"]))
    return out
