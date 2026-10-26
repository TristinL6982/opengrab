"""Bounded archive extraction that never overwrites the downloaded archive."""
from __future__ import annotations

import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable


class ArchiveError(ValueError):
    pass


DEFAULT_MAX_FILES = 10_000
DEFAULT_MAX_BYTES = 16 * 1024 * 1024 * 1024
MAX_PASSWORD_FILE_BYTES = 1024 * 1024
MAX_PASSWORDS = 1_000


def is_supported_archive(path: Path) -> bool:
    name = path.name.lower()
    return (name.endswith((".zip", ".cbz", ".tar", ".tar.gz", ".tgz",
                           ".tar.bz2", ".tbz2", ".tar.xz", ".txz")))


def _member_path(root: Path, name: str) -> Path:
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    windows_devices = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3",
                       "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
                       "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6",
                       "LPT7", "LPT8", "LPT9"}
    invalid = any(
        part in {"", ".", ".."} or ":" in part or part.rstrip(" .") != part
        or part.split(".")[0].upper() in windows_devices
        for part in pure.parts
    )
    if not normalized or pure.is_absolute() or invalid:
        raise ArchiveError(f"unsafe archive path: {name!r}")
    target = root.joinpath(*pure.parts)
    try:
        target.resolve().relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise ArchiveError(f"archive path escapes extraction folder: {name!r}") from exc
    return target


def _passwords(password_file: str) -> list[bytes]:
    if not password_file:
        return []
    path = Path(password_file).expanduser()
    if not path.is_file():
        raise ArchiveError("configured password list is not a regular file")
    if path.stat().st_size > MAX_PASSWORD_FILE_BYTES:
        raise ArchiveError("password file exceeds 1 MiB")
    raw = path.read_bytes()
    if len(raw) > MAX_PASSWORD_FILE_BYTES:
        raise ArchiveError("password file exceeds 1 MiB")
    values = [line.strip() for line in raw.decode("utf-8").splitlines()
              if line.strip()]
    if len(values) > MAX_PASSWORDS:
        raise ArchiveError("password file contains more than 1000 entries")
    return [value.encode("utf-8") for value in values]


def _copy_member(src, dst, expected: int, already_written: int,
                 max_bytes: int) -> int:
    written = 0
    while True:
        chunk = src.read(64 * 1024)
        if not chunk:
            break
        written += len(chunk)
        if written > expected or already_written + written > max_bytes:
            raise ArchiveError("archive member exceeds declared or configured size")
        dst.write(chunk)
    if written != expected:
        raise ArchiveError("archive member size does not match its metadata")
    return written


def _extract_zip(source: Path, stage: Path, max_files: int,
                 max_bytes: int, passwords: Iterable[bytes]) -> tuple[int, int]:
    with zipfile.ZipFile(source) as archive:
        all_entries = archive.infolist()
        if len(all_entries) > max_files:
            raise ArchiveError("archive contains too many files")
        entries = [entry for entry in all_entries if not entry.is_dir()]
        total = sum(entry.file_size for entry in entries)
        if total > max_bytes:
            raise ArchiveError("archive expands beyond the configured size limit")
        for entry in entries:
            _member_path(stage, entry.filename)
            mode = (entry.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ArchiveError("symbolic links are not extracted")
            if entry.file_size > max(1, entry.compress_size) * 1000:
                raise ArchiveError("archive member compression ratio exceeds 1000:1")
        encrypted = any(entry.flag_bits & 1 for entry in entries)
        attempts = list(passwords) if encrypted else [None]
        if encrypted and not attempts:
            raise ArchiveError("archive is encrypted; configure an opt-in password list")
        last_error = None
        for password in attempts:
            try:
                written_total = 0
                for entry in archive.infolist():
                    target = _member_path(stage, entry.filename)
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry, pwd=password) as src, target.open("xb") as dst:
                        written_total += _copy_member(
                            src, dst, entry.file_size, written_total, max_bytes)
                return len(entries), total
            except (RuntimeError, zipfile.BadZipFile) as exc:
                last_error = exc
                for child in list(stage.iterdir()):
                    if child.is_dir(): shutil.rmtree(child)
                    else: child.unlink()
        raise ArchiveError("no configured password unlocked the archive") from last_error


def _extract_tar(source: Path, stage: Path, max_files: int,
                 max_bytes: int) -> tuple[int, int]:
    with tarfile.open(source, mode="r:*") as archive:
        entries = archive.getmembers()
        if len(entries) > max_files:
            raise ArchiveError("archive contains too many files")
        files = [entry for entry in entries if entry.isfile()]
        total = sum(entry.size for entry in files)
        if total > max_bytes:
            raise ArchiveError("archive expands beyond the configured size limit")
        written_total = 0
        for entry in entries:
            _member_path(stage, entry.name)
            if not (entry.isfile() or entry.isdir()):
                raise ArchiveError("links and special files are not extracted")
        for entry in entries:
            target = _member_path(stage, entry.name)
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(entry)
            if stream is None:
                raise ArchiveError("could not read archive member")
            with stream, target.open("xb") as dst:
                written_total += _copy_member(
                    stream, dst, entry.size, written_total, max_bytes)
        return len(files), total


def extract_archive(source: Path, *, max_files: int = DEFAULT_MAX_FILES,
                    max_bytes: int = DEFAULT_MAX_BYTES,
                    password_file: str = "") -> dict:
    """Extract ZIP/TAR into a new sibling folder, with no overwrite or links."""
    source = Path(source).resolve(strict=True)
    if not source.is_file() or not is_supported_archive(source):
        raise ArchiveError("unsupported archive format")
    destination = source.with_suffix("")
    if destination.exists():
        raise ArchiveError("extraction folder already exists; refusing to overwrite")
    passwords = _passwords(password_file) if source.suffix.lower() in {".zip", ".cbz"} else []
    stage = Path(tempfile.mkdtemp(prefix=".opengrab-extract-", dir=source.parent))
    try:
        if source.suffix.lower() in {".zip", ".cbz"}:
            files, size = _extract_zip(source, stage, max_files, max_bytes, passwords)
        else:
            files, size = _extract_tar(source, stage, max_files, max_bytes)
        os.replace(stage, destination)
        return {"status": "extracted", "path": str(destination),
                "files": files, "expanded_bytes": size}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
