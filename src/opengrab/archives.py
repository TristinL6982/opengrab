"""Archive extraction extras (M11q).

Pure helpers for auto-extraction: recognising archive types, grouping
multi-part volume sets and checking they are complete, guarding against
path traversal and archive bombs, building password candidates from the
package password and an opt-in local password file, and building the
7-Zip command line. Nothing here starts a process or touches the disk.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_TAR_SUFFIXES = (".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".tar")
_PART_RAR = re.compile(r"^(?P<base>.+)\.part(?P<num>\d+)\.rar$", re.IGNORECASE)
_SPLIT = re.compile(r"^(?P<base>.+\.(?:7z|zip|rar))\.(?P<num>\d{3})$", re.IGNORECASE)
_OLD_RAR = re.compile(r"^(?P<base>.+)\.r(?P<num>\d{2})$", re.IGNORECASE)
_PLAIN_RAR = re.compile(r"^(?P<base>.+)\.rar$", re.IGNORECASE)


def archive_kind(name: str) -> Optional[str]:
    """Return "zip", "7z", "rar" or "tar" for an archive file name, else None."""
    low = os.path.basename(name).lower()
    if any(low.endswith(s) for s in _TAR_SUFFIXES):
        return "tar"
    m = _SPLIT.match(low)
    if m:
        return m.group("base").rsplit(".", 1)[1]
    if _OLD_RAR.match(low):
        return "rar"
    for ext in ("zip", "7z", "rar"):
        if low.endswith("." + ext):
            return ext
    return None


def volume_info(name: str) -> Optional[Tuple[str, int]]:
    """Return (set_key, index) for a volume of a multi-part set, else None.

    The first volume has index 1. For old-style RAR sets the .rar file is
    index 1 and .r00 is index 2. set_key is lower-cased.
    """
    low = os.path.basename(name).lower()
    m = _PART_RAR.match(low)
    if m:
        return (m.group("base") + ".rar", int(m.group("num")))
    m = _SPLIT.match(low)
    if m:
        return (m.group("base"), int(m.group("num")))
    m = _OLD_RAR.match(low)
    if m:
        return (m.group("base") + ".rar", int(m.group("num")) + 2)
    m = _PLAIN_RAR.match(low)
    if m:
        return (low, 1)
    return None


def group_volumes(names: Iterable[str]) -> Dict[str, List[str]]:
    """Group file names into volume sets, each sorted by volume index."""
    groups: Dict[str, List[Tuple[int, str]]] = {}
    for n in names:
        info = volume_info(n)
        if info is None:
            continue
        groups.setdefault(info[0], []).append((info[1], n))
    return {k: [n for _, n in sorted(v)] for k, v in groups.items()}


def is_set_complete(names: Sequence[str]) -> bool:
    """True when the volumes of one set are numbered 1..N with no gaps."""
    indexes = sorted(i for i in (volume_info(n) for n in names) if i is not None)
    nums = [i[1] for i in indexes]
    if not nums or len({i[0] for i in indexes}) != 1:
        return False
    return nums == list(range(1, len(nums) + 1))


def first_volume(names: Sequence[str]) -> Optional[str]:
    """The volume to hand to the extractor (index 1), or None."""
    for n in names:
        info = volume_info(n)
        if info is not None and info[1] == 1:
            return n
    return None


def safe_member_path(dest: str, member: str) -> str:
    """Join an archive member onto dest, refusing anything that escapes it."""
    cleaned = member.replace("\\", "/")
    if cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        raise ValueError(f"absolute path in archive: {member}")
    root = os.path.abspath(dest)
    target = os.path.abspath(os.path.join(root, *cleaned.split("/")))
    if target != root and not target.startswith(root + os.sep):
        raise ValueError(f"path escapes destination: {member}")
    return target


def check_limits(members: Iterable[Tuple[str, int]], max_files: int, max_expanded_gib: float) -> List[str]:
    """Return problems for an archive listing of (name, uncompressed size)."""
    items = list(members)
    problems: List[str] = []
    if len(items) > max_files:
        problems.append(f"too many files: {len(items)} > {max_files}")
    total = sum(max(0, int(size)) for _, size in items)
    limit = int(max_expanded_gib * 1024 ** 3)
    if total > limit:
        problems.append(f"expanded size {total} bytes exceeds {limit} bytes")
    return problems


def load_passwords(text: str) -> List[str]:
    """Parse a password file: one per line, '#' comments, unique, in order."""
    seen: List[str] = []
    for line in text.splitlines():
        pw = line.strip()
        if not pw or pw.startswith("#") or pw in seen:
            continue
        seen.append(pw)
    return seen


def password_candidates(package_password: str, file_passwords: Sequence[str]) -> List[str]:
    """Package password first, then the file list, without duplicates."""
    out: List[str] = []
    for pw in [package_password, *file_passwords]:
        if pw and pw not in out:
            out.append(pw)
    return out


def seven_zip_command(exe: str, archive: str, dest: str, password: str = "") -> List[str]:
    """Build the 7-Zip argv; an explicit -p stops 7-Zip from prompting."""
    return [exe, "x", "-y", "-o" + dest, "-p" + password, archive]