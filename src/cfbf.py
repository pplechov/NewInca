# -*- coding: utf-8 -*-
"""Minimal reader for Microsoft Compound File Binary Format (CFBF / OLE2),
as used by INCA 6.x .ipj project files.  Read-only.
"""
import struct, sys

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC


class DirEntry:
    __slots__ = ("sid", "name", "type", "color", "left", "right", "child",
                 "start", "size", "clsid", "mtime", "ctime")

    def __init__(self, sid, raw):
        (name_len,) = struct.unpack_from("<H", raw, 64)
        self.sid = sid
        n = max(0, name_len - 2)
        self.name = raw[0:n].decode("utf-16-le", "replace")
        self.type = raw[66]
        self.color = raw[67]
        self.left, self.right, self.child = struct.unpack_from("<III", raw, 68)
        self.clsid = raw[80:96].hex()
        self.ctime, self.mtime = struct.unpack_from("<QQ", raw, 100)
        self.start, = struct.unpack_from("<I", raw, 116)
        self.size, = struct.unpack_from("<Q", raw, 120)
        if self.type == 2:
            # v3 files: only low 32 bits are valid
            pass

    @property
    def kind(self):
        return {0: "unknown", 1: "storage", 2: "stream", 5: "root"}.get(self.type, "?%d" % self.type)

    def __repr__(self):
        return "<%s %r size=%d>" % (self.kind, self.name, self.size)


class CFBF:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        if d[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ValueError("not a CFBF file")
        self.minor, self.major = struct.unpack_from("<HH", d, 24)
        self.sector_shift, = struct.unpack_from("<H", d, 30)
        self.mini_shift, = struct.unpack_from("<H", d, 32)
        self.sector_size = 1 << self.sector_shift
        self.mini_size = 1 << self.mini_shift
        (self.num_fat,) = struct.unpack_from("<I", d, 44)
        (self.first_dir,) = struct.unpack_from("<I", d, 48)
        (self.mini_cutoff,) = struct.unpack_from("<I", d, 56)
        (self.first_minifat,) = struct.unpack_from("<I", d, 60)
        (self.num_minifat,) = struct.unpack_from("<I", d, 64)
        (self.first_difat,) = struct.unpack_from("<I", d, 68)
        (self.num_difat,) = struct.unpack_from("<I", d, 72)

        # DIFAT: 109 inline entries + chained sectors
        difat = list(struct.unpack_from("<109I", d, 76))
        sect = self.first_difat
        guard = 0
        while sect not in (ENDOFCHAIN, FREESECT) and guard < 10000:
            off = self._off(sect)
            per = self.sector_size // 4 - 1
            vals = struct.unpack_from("<%dI" % (self.sector_size // 4), d, off)
            difat.extend(vals[:per])
            sect = vals[per]
            guard += 1
        difat = [x for x in difat if x not in (FREESECT, ENDOFCHAIN)]

        # FAT
        fat = []
        for fs in difat[:self.num_fat]:
            off = self._off(fs)
            fat.extend(struct.unpack_from("<%dI" % (self.sector_size // 4), d, off))
        self.fat = fat
        self._difat_raw = difat

        # MiniFAT
        mf = []
        for s in self._chain(self.first_minifat, self.fat):
            off = self._off(s)
            mf.extend(struct.unpack_from("<%dI" % (self.sector_size // 4), d, off))
        self.minifat = mf

        # directory
        dirdata = b"".join(self._sector_bytes(s, self.fat) for s in self._chain(self.first_dir, self.fat))
        self.entries = []
        for i in range(len(dirdata) // 128):
            self.entries.append(DirEntry(i, dirdata[i * 128:(i + 1) * 128]))
        self.root = self.entries[0]

        # mini stream (root's stream)
        self.ministream = b""
        if self.root.size and self.root.start not in (ENDOFCHAIN, FREESECT):
            self.ministream = b"".join(self._sector_bytes(s, self.fat) for s in self._chain(self.root.start, self.fat))

    def _off(self, sect):
        return (sect + 1) * self.sector_size

    def _sector_bytes(self, sect, fat):
        off = self._off(sect)
        return self.data[off:off + self.sector_size]

    @staticmethod
    def _chain(start, fat):
        out = []
        s = start
        seen = set()
        while s not in (ENDOFCHAIN, FREESECT) and s < len(fat) and s not in seen:
            seen.add(s)
            out.append(s)
            s = fat[s]
        return out

    # ---- reading streams ----
    def stream_bytes(self, entry):
        if entry.size == 0:
            return b""
        if entry.size < self.mini_cutoff:
            out = []
            for s in self._chain(entry.start, self.minifat):
                off = s * self.mini_size
                out.append(self.ministream[off:off + self.mini_size])
            return b"".join(out)[:entry.size]
        data = b"".join(self._sector_bytes(s, self.fat) for s in self._chain(entry.start, self.fat))
        return data[:entry.size]

    def read_path(self, path):
        """path: list of names from root, case-insensitive."""
        e = self.root
        for name in path:
            found = None
            for e2 in self.entries:
                if e2.name.lower() == name.lower() and e2.sid != 0:
                    found = e2
                    break
            if found is None:
                raise KeyError(name)
            e = found
        return self.stream_bytes(e)

    # ---- tree ----
    def tree(self):
        """return list of (depth, entry) in hierarchical order using child/sibling links"""
        out = []

        def walk(sid, depth):
            seen = set()
            stack = [sid]
            order = []
            # collect all reachable via red-black tree links by simple BFS over left/right/child
            while stack:
                s = stack.pop()
                if s in seen or s >= len(self.entries) or s == 0xFFFFFFFF:
                    continue
                seen.add(s)
                e = self.entries[s]
                order.append(e)
                stack.append(e.left)
                stack.append(e.right)
            order.sort(key=lambda x: x.name.lower())
            for e in order:
                out.append((depth, e))
                if e.child not in (0xFFFFFFFF,) and e.type in (1, 5):
                    walk(e.child, depth + 1)

        walk(0, 0)
        return out


if __name__ == "__main__":
    c = CFBF(sys.argv[1])
    print("version %d.%d  sector=%d  mini=%d  fat_sectors=%d  entries=%d"
          % (c.major, c.minor, c.sector_size, c.mini_size, c.num_fat, len(c.entries)))
    for depth, e in c.tree():
        if e.type == 2:
            print("  " * depth + "%-40s STREAM  %10d bytes" % (e.name, e.size))
        else:
            print("  " * depth + "%-40s <%s>" % (e.name, e.kind))
