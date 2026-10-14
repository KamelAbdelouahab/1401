"""A FAT32 formatter in plain Python, for Linux PCs without dosfstools (mkfs.fat).

The layout follows Microsoft's FAT specification (fatgen103): 32 reserved sectors, two FATs, the root directory in
cluster 2, the FSInfo sector at 1 and the backup boot sector at 6. The cluster size follows the same table Windows'
own formatter uses for FAT32, so a stick made here reads like one Windows formatted. Only the empty file system is
written; the files go on afterwards through the kernel's own vfat driver.

    python3 -m p1401.fat32 <device-or-image> [--label 1401] [--hidden <first LBA>]
"""
import os
import struct
import sys
import time

RESERVED = 32
NUM_FATS = 2
MIN_CLUSTERS = 65525        # fewer clusters than this is FAT16 by definition, whatever the boot sector says
MAX_CLUSTERS = 0x0FFFFFF5


class FatError(RuntimeError):
    pass


def cluster_sectors(total_bytes, sector):
    """Sectors per cluster, from Microsoft's FAT32 table (4 KiB up to 8 GiB, 8 KiB to 16 GiB, 16 KiB to 32 GiB)."""
    if total_bytes <= 8 * (1 << 30):
        size = 4096
    elif total_bytes <= 16 * (1 << 30):
        size = 8192
    elif total_bytes <= 32 * (1 << 30):
        size = 16384
    else:
        size = 32768
    return max(1, size // sector)


def layout(total_sectors, sector=512):
    """(sectors per cluster, sectors per FAT, cluster count) for a volume of total_sectors."""
    spc = cluster_sectors(total_sectors * sector, sector)
    # fatgen103's estimate, FATSz = ceil((DskSize - RsvdSecCnt) / (128 * SecPerClus + NumFATs / 2)) for 512-byte sectors,
    # then grown until the FAT holds every cluster (the estimate can come up one sector short) and shrunk while it
    # still does (it can also be generous).
    entries_per_sector = sector // 4
    fat = -(-(total_sectors - RESERVED) * 2 // (2 * entries_per_sector * spc + NUM_FATS))

    def count(f):
        return (total_sectors - RESERVED - NUM_FATS * f) // spc
    while count(fat) + 2 > fat * entries_per_sector:
        fat += 1
    while fat > 1 and count(fat - 1) + 2 <= (fat - 1) * entries_per_sector:
        fat -= 1
    clusters = count(fat)
    if clusters < MIN_CLUSTERS:
        raise FatError(f"{total_sectors * sector / (1 << 20):.0f} MiB is too small for FAT32 ({clusters} clusters)")
    if clusters > MAX_CLUSTERS or clusters + 2 > fat * entries_per_sector:
        raise FatError("FAT32 layout does not fit (cluster count beyond the FAT)")
    return spc, fat, clusters


def _label(label):
    raw = (label or "NO NAME").upper().encode("ascii", "replace")[:11]
    return raw.ljust(11, b" ")


def boot_sector(total_sectors, sector, spc, fat, hidden, label, serial):
    b = bytearray(sector)
    b[0:3] = b"\xEB\x58\x90"
    b[3:11] = b"MSWIN4.1"
    struct.pack_into("<HBHBHHBHHHII", b, 11, sector, spc, RESERVED, NUM_FATS, 0, 0, 0xF8, 0, 63, 255, hidden,
                     total_sectors)
    # BPB_FATSz32, ExtFlags, FSVer, RootClus, FSInfo, BkBootSec, 12 reserved bytes
    struct.pack_into("<IHHIHH12x", b, 36, fat, 0, 0, 2, 1, 6)
    struct.pack_into("<BBBI11s8s", b, 64, 0x80, 0, 0x29, serial, _label(label), b"FAT32   ")
    # Not a system disk: a PC that boots it in Legacy mode is told so instead of hanging (OpenCore boots it via UEFI).
    msg = b"This is not a bootable disk. Start it in UEFI mode.\r\n\0"
    code = (b"\x0E\x1F\xBE\x77\x7C"  # push cs; pop ds; mov si, 0x7C77 (the message below)
            b"\xAC\x22\xC0\x74\x0B\x56\xB4\x0E\xBB\x07\x00\xCD\x10\x5E\xEB\xF0"  # print loop via int 10h
            b"\x32\xE4\xCD\x16\xCD\x19\xEB\xFE")  # wait for a key, then int 19h
    b[90:90 + len(code)] = code
    b[0x77:0x77 + len(msg)] = msg
    b[510:512] = b"\x55\xAA"
    return bytes(b)


def fsinfo_sector(sector, free, next_free):
    b = bytearray(sector)
    struct.pack_into("<I", b, 0, 0x41615252)
    struct.pack_into("<III", b, 484, 0x61417272, free, next_free)
    struct.pack_into("<I", b, 508, 0xAA550000)
    return bytes(b)


def format_volume(path, total_bytes=None, label="1401", hidden=0, sector=512, serial=None, say=None):
    """Writes an empty FAT32 file system to `path` (a partition or an image file). Returns a dict describing it."""
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        if total_bytes is None:
            total_bytes = os.lseek(fd, 0, os.SEEK_END)
        total = total_bytes // sector
        spc, fat, clusters = layout(total, sector)
        if serial is None:
            serial = int(time.time()) & 0xFFFFFFFF ^ int.from_bytes(os.urandom(4), "little")
        bs = boot_sector(total, sector, spc, fat, hidden, label, serial)
        info = fsinfo_sector(sector, clusters - 1, 3)
        # The reserved area: boot sector, FSInfo, the 3-sector boot record copy at 6, everything else zero.
        reserved = bytearray(RESERVED * sector)
        reserved[0:sector] = bs
        reserved[sector:2 * sector] = info
        reserved[6 * sector:7 * sector] = bs
        reserved[7 * sector:8 * sector] = info
        for s in (2, 8):  # a FAT32 boot record is three sectors; the third carries only its signature
            reserved[s * sector + 510:s * sector + 512] = b"\x55\xAA"
        os.lseek(fd, 0, os.SEEK_SET)
        _write(fd, bytes(reserved))
        first = bytearray(sector)
        struct.pack_into("<III", first, 0, 0x0FFFFFF8, 0x0FFFFFFF, 0x0FFFFFFF)  # media, end-of-chain, root cluster
        zero = bytes(1 << 20)
        fat_bytes = fat * sector
        for n in range(NUM_FATS):
            os.lseek(fd, (RESERVED + n * fat) * sector, os.SEEK_SET)
            _write(fd, bytes(first))
            left = fat_bytes - sector
            while left:
                k = min(left, len(zero))
                _write(fd, zero[:k])
                left -= k
            if say:
                say(f"FAT {n + 1} of {NUM_FATS} written")
        root = bytearray(spc * sector)
        root[0:11] = _label(label)
        root[11] = 0x08  # volume label entry
        os.lseek(fd, (RESERVED + NUM_FATS * fat) * sector, os.SEEK_SET)
        _write(fd, bytes(root))
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"sectors": total, "sector_size": sector, "cluster_sectors": spc, "fat_sectors": fat, "clusters": clusters,
            "serial": f"{serial >> 16:04X}-{serial & 0xFFFF:04X}"}


def _write(fd, data):
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise FatError("short write while formatting")
        view = view[n:]


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        sys.exit(2)

    def opt(name, default):
        return a[a.index(name) + 1] if name in a else default
    print(format_volume(a[0], label=opt("--label", "1401"), hidden=int(opt("--hidden", "0"))))
