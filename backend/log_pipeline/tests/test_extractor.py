from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from log_pipeline.ingest.extractor import Extractor, _UPLOAD_PREFIX_RE, _fix_zip_name


def test_fix_zip_name_decodes_gbk_when_utf8_flag_unset():
    raw = "中文.log".encode("gbk").decode("cp437")
    assert _fix_zip_name(raw, flag_bits=0) == "中文.log"


def test_fix_zip_name_passthrough_when_utf8_flag_set():
    assert _fix_zip_name("中文.log", flag_bits=0x800) == "中文.log"


def _make_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.flag_bits |= 0x800
            zf.writestr(info, data)


def _make_legacy_gbk_zip(path: Path, entries: dict[str, bytes]) -> None:
    """Hand-roll a zip with GBK-encoded filenames and the UTF-8 flag CLEARED.

    Python's zipfile auto-sets the 0x800 flag for non-ASCII names, so we have
    to emit local headers, file data, and central directory bytes ourselves to
    reproduce the legacy-encoding case seen in the real sample bundle.
    """
    local_blocks: list[bytes] = []
    central_blocks: list[bytes] = []
    file_offsets: list[int] = []
    cursor = 0

    for name, data in entries.items():
        name_bytes = name.encode("gbk")
        crc = zlib.crc32(data) & 0xFFFFFFFF
        size = len(data)
        # Local file header (PK\x03\x04, 30 bytes fixed + name + extra)
        local = (
            b"PK\x03\x04"
            + struct.pack(
                "<HHHHHIIIHH",
                20,    # version needed
                0,     # flags (UTF-8 bit cleared)
                0,     # method = stored
                0,     # mod time
                0,     # mod date
                crc,
                size,  # compressed size
                size,  # uncompressed size
                len(name_bytes),
                0,     # extra length
            )
            + name_bytes
        )
        file_offsets.append(cursor)
        local_blocks.append(local)
        local_blocks.append(data)
        cursor += len(local) + len(data)

    # Central directory (PK\x01\x02, 46 bytes fixed + name + extra + comment)
    for (name, data), offset in zip(entries.items(), file_offsets):
        name_bytes = name.encode("gbk")
        crc = zlib.crc32(data) & 0xFFFFFFFF
        size = len(data)
        cd = (
            b"PK\x01\x02"
            + struct.pack(
                "<HHHHHHIIIHHHHHII",
                20,    # version made by
                20,    # version needed
                0,     # flags
                0,     # method
                0,     # mod time
                0,     # mod date
                crc,
                size,  # compressed size
                size,  # uncompressed size
                len(name_bytes),
                0,     # extra length
                0,     # comment length
                0,     # disk number start
                0,     # internal attrs
                0,     # external attrs
                offset,
            )
            + name_bytes
        )
        central_blocks.append(cd)

    cd_blob = b"".join(central_blocks)
    cd_offset = cursor
    end = b"PK\x05\x06" + struct.pack(
        "<HHHHIIH",
        0,                    # disk number
        0,                    # disk with central dir
        len(entries),         # entries on this disk
        len(entries),         # total entries
        len(cd_blob),
        cd_offset,
        0,                    # comment length
    )
    path.write_bytes(b"".join(local_blocks) + cd_blob + end)


def test_extractor_basic(tmp_path: Path, work_root: Path):
    archive = tmp_path / "a.zip"
    _make_zip(archive, {"a/foo.log": b"hello", "a/bar.log": b"world"})
    files = list(Extractor(work_root).extract(archive))
    rels = sorted(f.relative_path for f in files)
    assert rels == ["a/bar.log", "a/foo.log"]
    contents = {f.relative_path: f.temp_path.read_bytes() for f in files}
    assert contents == {"a/foo.log": b"hello", "a/bar.log": b"world"}


def test_extractor_gbk_filename_fix(tmp_path: Path, work_root: Path):
    archive = tmp_path / "gbk.zip"
    _make_legacy_gbk_zip(
        archive,
        {"日志/娱乐系统日志/android/foo.log": b"x"},
    )
    # sanity-check our hand-rolled zip is parseable
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        assert len(infos) == 1
        # zipfile decoded the GBK bytes as cp437 (mojibake) because UTF-8 flag is off
        assert infos[0].flag_bits & 0x800 == 0

    files = list(Extractor(work_root).extract(archive))
    assert len(files) == 1
    assert files[0].relative_path == "日志/娱乐系统日志/android/foo.log"
    assert files[0].temp_path.read_bytes() == b"x"


def test_extractor_skips_ds_store(tmp_path: Path, work_root: Path):
    archive = tmp_path / "ds.zip"
    _make_zip(
        archive,
        {
            "x/.DS_Store": b"junk",
            "__MACOSX/x/._foo": b"junk",
            "x/keep.log": b"keep",
        },
    )
    rels = sorted(f.relative_path for f in Extractor(work_root).extract(archive))
    assert rels == ["x/keep.log"]


def test_extractor_recurses_into_nested_zip(tmp_path: Path, work_root: Path):
    inner = tmp_path / "inner.zip"
    _make_zip(inner, {"deep/a.log": b"AAA", "deep/b.log": b"BBB"})
    outer = tmp_path / "outer.zip"
    _make_zip(outer, {"top/wrap.zip": inner.read_bytes(), "top/note.txt": b"hi"})
    files = list(Extractor(work_root).extract(outer))
    rels = sorted(f.relative_path for f in files)
    assert rels == [
        "top/note.txt",
        "top/wrap.zip/deep/a.log",
        "top/wrap.zip/deep/b.log",
    ]
    by_rel = {f.relative_path: f for f in files}
    assert by_rel["top/wrap.zip/deep/a.log"].nested_depth == 1
    assert by_rel["top/wrap.zip/deep/a.log"].temp_path.read_bytes() == b"AAA"
    assert by_rel["top/note.txt"].nested_depth == 0


# ---------------------------------------------------------------------------
# _UPLOAD_PREFIX_RE — UUID prefix stripping
# ---------------------------------------------------------------------------

def test_upload_prefix_re_strips_uuid_prefix():
    uuid_part = "a" * 32
    assert _UPLOAD_PREFIX_RE.sub("", f"{uuid_part}__fota.log") == "fota.log"


def test_upload_prefix_re_strips_mixed_hex_uuid():
    uuid_part = "0a1b2c3d4e5f6789abcdef0123456789"
    assert _UPLOAD_PREFIX_RE.sub("", f"{uuid_part}__system.log") == "system.log"


def test_upload_prefix_re_passthrough_no_prefix():
    assert _UPLOAD_PREFIX_RE.sub("", "fota_2025.log") == "fota_2025.log"


def test_upload_prefix_re_does_not_strip_short_hex_prefix():
    # 31 hex chars + __ is NOT a full UUID prefix — must NOT strip
    short = "a" * 31 + "__fota.log"
    assert _UPLOAD_PREFIX_RE.sub("", short) == short


# ---------------------------------------------------------------------------
# _extract_plain — single file passthrough
# ---------------------------------------------------------------------------

def test_extractor_plain_log_file(tmp_path: Path, work_root: Path):
    f = tmp_path / "sysdump.log"
    f.write_bytes(b"line1\nline2\n")
    files = list(Extractor(work_root).extract(f))
    assert len(files) == 1
    assert files[0].relative_path == "sysdump.log"
    assert files[0].temp_path.read_bytes() == b"line1\nline2\n"
    assert files[0].nested_depth == 0


def test_extractor_plain_strips_uuid_prefix(tmp_path: Path, work_root: Path):
    uuid_part = "b" * 32
    stored = tmp_path / f"{uuid_part}__fota_2025-09-12.log"
    stored.write_bytes(b"fota log content")
    files = list(Extractor(work_root).extract(stored))
    assert len(files) == 1
    assert files[0].relative_path == "fota_2025-09-12.log"
    assert files[0].temp_path.read_bytes() == b"fota log content"


def test_extractor_plain_no_uuid_prefix_preserved(tmp_path: Path, work_root: Path):
    f = tmp_path / "plain_name.txt"
    f.write_bytes(b"some text")
    files = list(Extractor(work_root).extract(f))
    assert files[0].relative_path == "plain_name.txt"


def test_extractor_plain_dlt_file(tmp_path: Path, work_root: Path):
    f = tmp_path / "trace.dlt"
    f.write_bytes(b"\x44\x4c\x54\x01binary dlt content")
    files = list(Extractor(work_root).extract(f))
    assert len(files) == 1
    assert files[0].relative_path == "trace.dlt"


# ---------------------------------------------------------------------------
# _extract_rar — RAR extraction (rarfile.RarFile mocked)
# ---------------------------------------------------------------------------

def _fake_rf_ctx(infolist, file_contents: dict[str, bytes]) -> MagicMock:
    """Build a context-manager mock for rarfile.RarFile."""
    rf = MagicMock()
    rf.infolist.return_value = infolist

    def _open(info):
        cm = MagicMock()
        cm.__enter__ = lambda s: io.BytesIO(file_contents.get(info.filename, b""))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    rf.open.side_effect = _open
    rf.__enter__ = lambda s: rf
    rf.__exit__ = MagicMock(return_value=False)
    return rf


def _make_rar_info(filename: str, is_dir: bool = False) -> MagicMock:
    info = MagicMock()
    info.filename = filename
    info.is_dir.return_value = is_dir
    return info


def test_extractor_rar_basic(tmp_path: Path, work_root: Path):
    content = b"ECU update log line"
    fake_info = _make_rar_info("logs/ecu.log")
    rf = _fake_rf_ctx([fake_info], {"logs/ecu.log": content})

    archive = tmp_path / "test.rar"
    archive.write_bytes(b"placeholder")

    with patch("log_pipeline.ingest.extractor.shutil.which", return_value="/usr/bin/unrar"), \
         patch("rarfile.RarFile", return_value=rf):
        files = list(Extractor(work_root).extract(archive))

    assert len(files) == 1
    assert files[0].relative_path == "logs/ecu.log"
    assert files[0].temp_path.read_bytes() == content
    assert files[0].nested_depth == 0


def test_extractor_rar_skips_directory_entries(tmp_path: Path, work_root: Path):
    dir_info = _make_rar_info("logs/", is_dir=True)
    file_info = _make_rar_info("logs/fota.log")
    rf = _fake_rf_ctx([dir_info, file_info], {"logs/fota.log": b"x"})

    archive = tmp_path / "dirs.rar"
    archive.write_bytes(b"placeholder")

    with patch("log_pipeline.ingest.extractor.shutil.which", return_value="/usr/bin/unrar"), \
         patch("rarfile.RarFile", return_value=rf):
        files = list(Extractor(work_root).extract(archive))

    rels = [f.relative_path for f in files]
    assert "logs/" not in rels
    assert "logs/fota.log" in rels


def test_extractor_rar_normalizes_windows_backslash_paths(tmp_path: Path, work_root: Path):
    content = b"win content"
    fake_info = _make_rar_info("a\\b\\c.log")  # Windows-style paths from WinRAR
    rf = _fake_rf_ctx([fake_info], {"a\\b\\c.log": content})

    archive = tmp_path / "win.rar"
    archive.write_bytes(b"placeholder")

    with patch("log_pipeline.ingest.extractor.shutil.which", return_value="/usr/bin/unrar"), \
         patch("rarfile.RarFile", return_value=rf):
        files = list(Extractor(work_root).extract(archive))

    assert len(files) == 1
    assert files[0].relative_path == "a/b/c.log"


def test_extractor_rar_nested_zip_expands(tmp_path: Path, work_root: Path):
    """RAR containing a .zip is recursively expanded."""
    inner = tmp_path / "inner.zip"
    _make_zip(inner, {"deep/data.log": b"DEEP"})
    zip_bytes = inner.read_bytes()

    fake_info = _make_rar_info("wrap.zip")
    rf = _fake_rf_ctx([fake_info], {"wrap.zip": zip_bytes})

    archive = tmp_path / "outer.rar"
    archive.write_bytes(b"placeholder")

    with patch("log_pipeline.ingest.extractor.shutil.which", return_value="/usr/bin/unrar"), \
         patch("rarfile.RarFile", return_value=rf):
        files = list(Extractor(work_root).extract(archive))

    rels = [f.relative_path for f in files]
    assert "wrap.zip/deep/data.log" in rels
    nested = next(f for f in files if f.relative_path == "wrap.zip/deep/data.log")
    assert nested.nested_depth == 1
    assert nested.temp_path.read_bytes() == b"DEEP"


# _extract_rar — platform detection tests

def test_extractor_rar_no_tool_raises_runtime_error(tmp_path: Path, work_root: Path):
    """When neither unrar nor unar is in PATH, a RuntimeError with install hint is raised."""
    archive = tmp_path / "no_tool.rar"
    archive.write_bytes(b"placeholder")

    with patch("log_pipeline.ingest.extractor.shutil.which", return_value=None), \
         patch("log_pipeline.ingest.extractor.platform.system", return_value="Linux"):
        import pytest as _pytest
        with _pytest.raises(RuntimeError, match="No RAR decompression tool found"):
            list(Extractor(work_root).extract(archive))


def test_extractor_rar_no_tool_macos_hint(tmp_path: Path, work_root: Path):
    """Error message on macOS mentions brew install unar."""
    archive = tmp_path / "mac.rar"
    archive.write_bytes(b"placeholder")

    with patch("log_pipeline.ingest.extractor.shutil.which", return_value=None), \
         patch("log_pipeline.ingest.extractor.platform.system", return_value="Darwin"):
        import pytest as _pytest
        with _pytest.raises(RuntimeError, match="brew install unar"):
            list(Extractor(work_root).extract(archive))


def test_extractor_rar_macos_falls_back_to_unar(tmp_path: Path, work_root: Path):
    """On macOS with only unar available, rarfile.UNRAR_TOOL is set to UNAR_TOOL."""
    import rarfile as _rarfile

    content = b"mac log line"
    fake_info = _make_rar_info("mac.log")
    rf = _fake_rf_ctx([fake_info], {"mac.log": content})

    archive = tmp_path / "mac.rar"
    archive.write_bytes(b"placeholder")

    original_tool = _rarfile.UNRAR_TOOL
    try:
        # Simulate macOS: unrar absent, unar present
        def _which(cmd: str) -> str | None:
            return "/usr/local/bin/unar" if cmd == _rarfile.UNAR_TOOL else None

        with patch("log_pipeline.ingest.extractor.shutil.which", side_effect=_which), \
             patch("log_pipeline.ingest.extractor.platform.system", return_value="Darwin"), \
             patch("rarfile.RarFile", return_value=rf):
            files = list(Extractor(work_root).extract(archive))

        # After the call, UNRAR_TOOL should have been switched to UNAR_TOOL
        assert _rarfile.UNRAR_TOOL == _rarfile.UNAR_TOOL
        assert len(files) == 1
        assert files[0].relative_path == "mac.log"
    finally:
        _rarfile.UNRAR_TOOL = original_tool  # restore global state
