"""Create lossless Windows checkpoints and validate Cargo's vendored inputs."""
import hashlib
import gzip
import json
import os
import re
import stat
from decimal import Decimal
from pathlib import Path
import tarfile
import tempfile


class CheckpointWriter(tarfile.TarFile):
    """Write Unicode PAX headers with exact times and native directory links."""

    def gettarinfo(self, name=None, arcname=None, fileobj=None):
        member = super().gettarinfo(name, arcname, fileobj)
        details = os.fstat(fileobj.fileno()) if fileobj else os.lstat(name)
        if member is None:
            raise ValueError(f"Unsupported checkpoint input: {name}")
        if os.name == "nt" and details.st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT:
            # pnpm junctions must remain directory links, not expanded trees.
            member.type, member.size, member.linkname = tarfile.SYMTYPE, 0, os.readlink(name)
        if not (member.isreg() or member.isdir() or member.issym() or member.islnk()):
            raise ValueError(f"Unsupported checkpoint input: {name}")
        member.pax_headers["mtime"] = str(Decimal(details.st_mtime_ns) / 10**9)
        if member.issym():
            directory = (bool(details.st_file_attributes & stat.FILE_ATTRIBUTE_DIRECTORY)
                         if os.name == "nt" else os.path.isdir(name))
            member.pax_headers["LIBARCHIVE.symlinktype"] = "dir" if directory else "file"
            if os.path.isabs(member.linkname) and getattr(self, "checkpoint_root", None):
                target = canonical_path(member.linkname)
                if target.is_relative_to(self.checkpoint_root):
                    member.linkname = os.path.relpath(target, canonical_path(Path(name).parent))
        if member.isreg() and details.st_nlink <= 1:
            self.inodes.pop((details.st_ino, details.st_dev), None)
        return member

    def addfile(self, member, fileobj=None):
        before = os.fstat(fileobj.fileno()) if fileobj else None
        if before and (before.st_size != member.size or
                       before.st_mtime_ns != int(Decimal(member.pax_headers["mtime"]) * 10**9)):
            raise RuntimeError(f"Checkpoint input changed before reading: {member.name}")
        super().addfile(member, fileobj)
        if before:
            after = os.fstat(fileobj.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"Checkpoint input changed while reading: {member.name}")
        # tarfile retains every output header by default. Only its hard-link
        # inode map is needed while writing a multi-million-member checkpoint.
        self.members.clear()


def canonical_path(path):
    name = os.path.realpath(path)
    if os.name == "nt":
        if name.startswith("\\\\?\\UNC\\"):
            name = "\\\\" + name[8:]
        elif name.startswith("\\\\?\\"):
            name = name[4:]
    return Path(name)


def checkpoint_filter(member, destination):
    if (member.issym() or member.islnk()) and os.path.isabs(member.linkname):
        root = canonical_path(destination)
        target = canonical_path(member.linkname)
        if target.is_relative_to(root):
            base = (root / member.name).parent if member.issym() else root
            member = member.replace(linkname=os.path.relpath(target, base), deep=False)
        elif member.issym() and member.pax_headers.get("LIBARCHIVE.symlinktype") != "dir":
            alias = re.fullmatch(r"(?:\./)?src/out/Sync/siso\.exe(?:\.\d+)?\.(INFO|WARNING|ERROR|FATAL)", member.name)
            temporary = [tempfile.gettempdir(), *[os.environ.get(key) for key in ("TEMP", "TMP", "RUNNER_TEMP")]]
            if os.name == "nt":
                temporary.append(str(Path.home() / "AppData/Local/Temp"))
            if (alias and not target.is_dir()
                    and re.fullmatch(r"siso\.exe\..+\.log\." + alias[1] + r"\.\d{8}-\d{6}\.\d+", target.name)
                    and any(target.parent == canonical_path(folder) for folder in temporary if folder)):
                # Siso recreates these file aliases on its next invocation. Keep
                # the pointer only; never read or extract its external log target.
                return tarfile.tar_filter(member, destination)
    return tarfile.data_filter(member, destination)


class CheckpointTar(tarfile.TarFile):
    """Use Windows Unicode APIs without losing PAX timestamps or link types."""

    def utime(self, member, targetpath):
        if member.mtime is not None:
            # TarInfo.mtime is a float; its original PAX text retains all digits.
            timestamp = int(Decimal(member.pax_headers.get("mtime", str(member.mtime))) * 10**9)
            os.utime(targetpath, ns=(timestamp, timestamp))

    def makelink(self, member, targetpath):
        # Python versions before the extraction-filter security update call this.
        self.makelink_with_filter(member, targetpath, None, None)

    def makelink_with_filter(self, member, targetpath, filter_function, extraction_root):
        if member.issym():
            link_type = member.pax_headers.get("LIBARCHIVE.symlinktype")
            if link_type not in (None, "file", "dir"):
                raise ValueError(f"Invalid checkpoint symlink type: {member.name}")
            directory = (link_type == "dir" if link_type else
                         (Path(targetpath).parent / member.linkname).is_dir())
            # Never fall back to copying a link target: pnpm needs real links.
            os.symlink(member.linkname, targetpath, target_is_directory=directory)
        else:
            os.link(member._link_target, targetpath)


def extract_archive(archive, root):
    if root.exists():
        raise ValueError(f"Refusing to restore over existing build tree: {root}")
    root.mkdir(parents=True)
    # Streaming avoids retaining 1.6 million headers in memory. Only Siso's
    # temporary log-file aliases may point outside the restore tree.
    with CheckpointTar.open(archive, "r|gz", stream=True, errorlevel=2) as stream:
        stream.extractall(root, filter=checkpoint_filter)
    print("Restored checkpoint with Unicode names, PAX timestamps and native links", flush=True)


def validate_archive(archive, *, restore_root=None):
    """Read every tar member and gzip trailer, including data after tar EOF."""
    class Reader:
        def __init__(self, stream):
            self.stream = stream
            self.tail = b""
            self.size = 0

        def read(self, size=-1):
            data = self.stream.read(size)
            self.tail = (self.tail + data)[-1024:]
            self.size += len(data)
            return data

    try:
        with gzip.open(archive, "rb") as compressed:
            reader = Reader(compressed)
            with tarfile.open(fileobj=reader, mode="r|", stream=True) as stream:
                count = 0
                data_end = 0
                for member in stream:
                    count += 1
                    if restore_root is not None and (member.issym() or member.islnk()):
                        checkpoint_filter(member, restore_root)
                    data_end = max(data_end, member.offset_data + ((member.size + 511) // 512) * 512)
            while reader.read(1024 * 1024):
                pass
            if (not count or reader.size < data_end + 1024 or reader.size % 512 or
                    reader.tail != b"\0" * 1024):
                raise ValueError("Missing tar end markers")
    except (EOFError, OSError, tarfile.TarError, ValueError) as error:
        raise ValueError(f"Invalid checkpoint archive: {error}") from error
    print(f"Validated checkpoint: {count} members, complete tar and gzip streams", flush=True)
    return count


def verify_checkpoint(archive, allow_legacy=False, *, restore_root=None):
    manifest = archive.with_name("state-manifest.json")
    if not manifest.exists():
        if not allow_legacy:
            raise ValueError("Checkpoint integrity manifest is missing")
        print("Legacy recovery: validating the entire archive before extraction", flush=True)
        return validate_archive(archive, restore_root=restore_root)
    expected = json.loads(manifest.read_text(encoding="utf-8"))
    if expected.get("format") != 1 or archive.stat().st_size != expected.get("bytes"):
        raise ValueError("Checkpoint size/format mismatch; download is incomplete")
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected.get("sha256"):
        raise ValueError("Checkpoint SHA256 mismatch; refusing extraction")
    count = validate_archive(archive, restore_root=restore_root)
    if count != expected.get("members"):
        raise ValueError("Checkpoint member count mismatch")
    return count


def verify_wasm_vendor(brave):
    vendor = brave / "third_party/wasm/vendor"
    manifests = sorted(vendor.glob("*/.cargo-checksum.json"))
    if not manifests:
        raise ValueError(f"Missing vendored Cargo checksum manifests: {vendor}")
    count = 0
    for manifest in manifests:
        for name, expected in json.loads(manifest.read_text(encoding="utf-8"))["files"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Invalid Cargo checksum path: {name}")
            path = manifest.parent / relative
            if not path.is_file():
                raise ValueError(f"Missing vendored Cargo input: {path}")
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise ValueError(f"Vendored Cargo checksum mismatch: {path}")
            count += 1
    print(f"Verified {count} vendored Cargo files across {len(manifests)} crates", flush=True)
    return count


def create_archive(root, archive):
    root = root.resolve()
    archive = archive.resolve()
    if archive.is_relative_to(root):
        raise ValueError("Checkpoint output must be outside the archived tree")
    archive.parent.mkdir(parents=True, exist_ok=True)
    print("Creating Unicode PAX checkpoint with gzip compression level 1", flush=True)
    checksum = hashlib.sha256()

    class HashedOutput:
        def write(self, data):
            written = output.write(data)
            if written != len(data):
                raise OSError("Incomplete checkpoint write")
            checksum.update(data)
            return written

        def flush(self):
            output.flush()

    with archive.open("xb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=HashedOutput(), compresslevel=1, mtime=0) as compressed:
            with CheckpointWriter.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT,
                                       dereference=False, copybufsize=1024 * 1024) as stream:
                stream.checkpoint_root = canonical_path(root)
                stream.add(root, arcname=".")
    count = validate_archive(archive)
    archive.with_name("state-manifest.json").write_text(json.dumps({
        "format": 1, "bytes": archive.stat().st_size,
        "sha256": checksum.hexdigest(), "members": count,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Checkpoint: {archive.stat().st_size / 2**30:.2f} GiB", flush=True)
