from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional
from uuid import UUID

from log_pipeline.decoders.android_logcat import parse_logcat_timestamp
from log_pipeline.decoders.base import infer_year_hint
from log_pipeline.decoders.fota_text import parse_fota_timestamp
from log_pipeline.decoders.kernel import parse_dmesg_relative
from log_pipeline.decoders.mcu_text import parse_mcu_tick
from log_pipeline.decoders.tbox_text import parse_generic_timestamp
from log_pipeline.index.file_index import read_bucket_index
from log_pipeline.interfaces import (
    BUCKET_SECONDS,
    AlignmentMethod,
    BootSegment,
    ControllerType,
    LogFileMeta,
)
from log_pipeline.prescan.prescanner import parse_dlt_decoded_timestamp
from log_pipeline.query.slim_filter import SlimFilter
from log_pipeline.storage.catalog import Catalog

logger = logging.getLogger(__name__)


_DEFAULT_LIMIT = 1_000_000
_HARD_LIMIT = 5_000_000


@dataclass
class RangeQueryParams:
    bundle_id: UUID
    start: float                   # aligned timestamp (tbox clock), seconds
    end: float
    controllers: Optional[list[ControllerType]] = None
    format: str = "full"           # "full" | "slim"
    include_unsynced: bool = False
    limit: int = _DEFAULT_LIMIT


@dataclass
class RangeQueryStats:
    files_scanned: int = 0
    lines_emitted: int = 0
    truncated: bool = False
    matched_files: list[LogFileMeta] = field(default_factory=list)


def _parse_line_ts(meta: LogFileMeta, year_hint: int, text: str) -> Optional[float]:
    # Files that wrote a separate decoded.log (DLT, iBDU) all use the same
    # ``YYYY-MM-DDTHH:MM:SS.mmm`` ISO prefix in the decoded form.
    if meta.decoded_path and meta.decoded_path != meta.stored_path:
        return parse_dlt_decoded_timestamp(text)
    if meta.controller == ControllerType.ANDROID:
        return parse_logcat_timestamp(text, year_hint)
    if meta.controller == ControllerType.FOTA:
        return parse_fota_timestamp(text)
    if meta.controller == ControllerType.KERNEL:
        ts = parse_dmesg_relative(text)
        if ts is not None:
            return ts
        return parse_logcat_timestamp(text, year_hint)
    if meta.controller == ControllerType.TBOX:
        return parse_generic_timestamp(text)
    if meta.controller == ControllerType.MCU:
        # ``&<tick_ms> <SEV>@<MOD>:`` — the decoder wrote raw_ts = tick/1000
        # so we must mirror that here for windowed-line filtering.
        ts = parse_mcu_tick(text)
        if ts is not None:
            return ts
        return parse_generic_timestamp(text)
    return None


def _seek_to_bucket(idx_path: Path, raw_start: float) -> tuple[int, int]:
    """Linear scan of the bucket index to find the latest record at or before
    ``raw_start``'s bucket. Returns (byte_offset, line_no_start)."""
    target_bucket = int(raw_start // BUCKET_SECONDS)
    last = (0, 0)
    for bucket_id, byte_offset, line_no in read_bucket_index(idx_path):
        if bucket_id > target_bucket:
            break
        last = (byte_offset, line_no)
    return last


def _byte_window_for_range(
    idx_path: Path, raw_start: float, raw_end: float
) -> tuple[int, int, Optional[int]]:
    """Single pass over the bucket index returning ``(start_byte, start_line,
    end_byte)``. ``end_byte`` is the offset of the first bucket strictly past
    ``raw_end + BUCKET_SECONDS`` (one-bucket lookahead) so we stop reading well
    before the EOF for partial-window queries; ``None`` means read to EOF."""
    target_start = int(raw_start // BUCKET_SECONDS)
    target_end = int(raw_end // BUCKET_SECONDS) + 1  # one-bucket lookahead for jitter
    start = (0, 0)
    end_byte: Optional[int] = None
    for bucket_id, byte_offset, line_no in read_bucket_index(idx_path):
        if bucket_id <= target_start:
            start = (byte_offset, line_no)
        elif bucket_id > target_end:
            end_byte = byte_offset
            break
    return start[0], start[1], end_byte


def _file_overlaps(meta: LogFileMeta, aligned_start: float, aligned_end: float) -> bool:
    """Does the file's valid-ts range, shifted by clock_offset, overlap [start, end]?
    Files without offset are deferred to ``include_unsynced`` handling and not
    considered for normal-window matching.

    Segmented files store ``valid_ts_min/max`` already in aligned space — no
    further shift; non-segmented files keep raw + offset semantics.
    """
    if meta.valid_ts_min is None or meta.valid_ts_max is None:
        return False
    if meta.offset_method == AlignmentMethod.SEGMENTED:
        return meta.valid_ts_max >= aligned_start and meta.valid_ts_min <= aligned_end
    if meta.clock_offset is None:
        # Files whose raw timestamps already passed is_effective_wall_clock_ts (e.g.
        # FOTA text logs with 2025 dates) are stored with valid_ts_min/max in wall-
        # clock space. No offset needed — use them directly.
        if meta.valid_ts_min is None or meta.valid_ts_max is None:
            return False
        return meta.valid_ts_max >= aligned_start and meta.valid_ts_min <= aligned_end
    file_aligned_min = meta.valid_ts_min + meta.clock_offset
    file_aligned_max = meta.valid_ts_max + meta.clock_offset
    return file_aligned_max >= aligned_start and file_aligned_min <= aligned_end


def _line_in_unsynced(line_no: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    for start, end in ranges:
        if start <= line_no <= end:
            return True
    return False


class RangeQuery:
    def __init__(
        self,
        catalog: Catalog,
        slim_filter: SlimFilter | None = None,
    ):
        self._catalog = catalog
        self._slim = slim_filter or SlimFilter.empty()

    def stream(self, params: RangeQueryParams) -> Iterator[dict]:
        """Yield NDJSON-ready dicts. Caller is responsible for serialisation."""
        files = self._catalog.list_files_by_bundle(params.bundle_id)
        if params.controllers:
            allow = set(params.controllers)
            files = [f for f in files if f.controller in allow]
        files = [f for f in files if f.decoded_path]

        # split into "in-window with offset" and "no-offset" buckets so we can
        # serve aligned-window matches first, then optionally append unsynced files.
        windowed = [f for f in files if _file_overlaps(f, params.start, params.end)]
        # SEGMENTED files have NULL clock_offset by convention but are NOT
        # unsynced — exclude them from the unsynced bucket.
        # Similarly, files with valid_ts_min already in wall-clock space are
        # already in `windowed` after the _file_overlaps fix — skip them here
        # to avoid double-emitting.
        unsynced_files = (
            [
                f for f in files
                if f.clock_offset is None
                and f.offset_method != AlignmentMethod.SEGMENTED
                and (f.valid_ts_min is None or f.valid_ts_max is None)
            ]
            if params.include_unsynced
            else []
        )

        stats = RangeQueryStats(files_scanned=len(windowed) + len(unsynced_files))
        limit = min(params.limit, _HARD_LIMIT)
        emitted = 0

        for meta in windowed:
            if emitted >= limit:
                stats.truncated = True
                break
            stream = (
                self._stream_segmented_file(meta, params)
                if meta.offset_method == AlignmentMethod.SEGMENTED
                else self._stream_windowed_file(meta, params)
            )
            for record in stream:
                if emitted >= limit:
                    stats.truncated = True
                    break
                yield record
                emitted += 1
            stats.matched_files.append(meta)

        if not stats.truncated:
            for meta in unsynced_files:
                if emitted >= limit:
                    stats.truncated = True
                    break
                for record in self._stream_unsynced_file(meta, params):
                    if emitted >= limit:
                        stats.truncated = True
                        break
                    yield record
                    emitted += 1
                stats.matched_files.append(meta)

        stats.lines_emitted = emitted
        # final marker yielded after data — consumers can ignore via "_meta" key
        yield {
            "_meta": True,
            "files_scanned": stats.files_scanned,
            "lines_emitted": stats.lines_emitted,
            "truncated": stats.truncated,
        }

    def _stream_windowed_file(
        self, meta: LogFileMeta, params: RangeQueryParams
    ) -> Iterator[dict]:
        offset = meta.clock_offset or 0.0
        raw_start = params.start - offset
        raw_end = params.end - offset
        decoded = Path(meta.decoded_path)  # type: ignore[arg-type]
        if not decoded.is_file():
            return
        idx_path = Path(meta.bucket_index_path) if meta.bucket_index_path else None
        if idx_path is None or not idx_path.is_file():
            seek_byte, line_no, end_byte = 0, 0, None
        else:
            seek_byte, line_no, end_byte = _byte_window_for_range(idx_path, raw_start, raw_end)

        year_hint = infer_year_hint(Path(meta.stored_path)) if meta.stored_path else 2025

        with open(decoded, "rb") as f:
            f.seek(seek_byte)
            cur_offset = seek_byte
            cur_line = line_no
            for raw in f:
                if end_byte is not None and cur_offset >= end_byte:
                    break
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                raw_ts = _parse_line_ts(meta, year_hint, text)
                in_unsynced = _line_in_unsynced(cur_line, meta.unsynced_line_ranges)
                aligned_ts: Optional[float]
                if raw_ts is None or in_unsynced:
                    aligned_ts = None
                else:
                    aligned_ts = raw_ts + offset
                    if aligned_ts < params.start or aligned_ts > params.end:
                        cur_offset += len(raw)
                        cur_line += 1
                        continue
                if aligned_ts is None and not params.include_unsynced:
                    cur_offset += len(raw)
                    cur_line += 1
                    continue
                if params.format == "slim" and not self._slim.keep(meta.controller, text):
                    cur_offset += len(raw)
                    cur_line += 1
                    continue
                yield self._record(meta, cur_line, raw_ts, aligned_ts, text)
                cur_offset += len(raw)
                cur_line += 1

    def _stream_segmented_file(
        self, meta: LogFileMeta, params: RangeQueryParams
    ) -> Iterator[dict]:
        """For multi-boot files (MCU): walk segment by segment, seek to each
        segment's byte range, parse ``&<tick>`` per line and align via the
        segment's own ``clock_offset``. No bucket index — sequential read of
        only the segments that overlap the window."""
        decoded = Path(meta.decoded_path)  # type: ignore[arg-type]
        if not decoded.is_file():
            return
        for seg in meta.segments:
            if seg.clock_offset is None or seg.raw_ts_min is None or seg.raw_ts_max is None:
                continue
            seg_aligned_min = seg.raw_ts_min + seg.clock_offset
            seg_aligned_max = seg.raw_ts_max + seg.clock_offset
            if seg_aligned_max < params.start or seg_aligned_min > params.end:
                continue
            with open(decoded, "rb") as f:
                f.seek(seg.byte_start)
                cur_offset = seg.byte_start
                cur_line = seg.line_start
                for raw in f:
                    if cur_offset >= seg.byte_end:
                        break
                    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    raw_ts = _parse_line_ts(meta, 2025, text)
                    aligned_ts: Optional[float]
                    if raw_ts is None:
                        aligned_ts = None
                    else:
                        aligned_ts = raw_ts + seg.clock_offset
                        if aligned_ts < params.start or aligned_ts > params.end:
                            cur_offset += len(raw)
                            cur_line += 1
                            continue
                    if aligned_ts is None and not params.include_unsynced:
                        cur_offset += len(raw)
                        cur_line += 1
                        continue
                    if params.format == "slim" and not self._slim.keep(meta.controller, text):
                        cur_offset += len(raw)
                        cur_line += 1
                        continue
                    yield self._record(meta, cur_line, raw_ts, aligned_ts, text)
                    cur_offset += len(raw)
                    cur_line += 1

    def _stream_unsynced_file(
        self, meta: LogFileMeta, params: RangeQueryParams
    ) -> Iterator[dict]:
        decoded = Path(meta.decoded_path)  # type: ignore[arg-type]
        if not decoded.is_file():
            return
        year_hint = infer_year_hint(Path(meta.stored_path)) if meta.stored_path else 2025
        cur_offset = 0
        cur_line = 0
        with open(decoded, "rb") as f:
            for raw in f:
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                raw_ts = _parse_line_ts(meta, year_hint, text)
                if params.format == "slim" and not self._slim.keep(meta.controller, text):
                    cur_offset += len(raw)
                    cur_line += 1
                    continue
                yield self._record(meta, cur_line, raw_ts, None, text, clock_unaligned=True)
                cur_offset += len(raw)
                cur_line += 1

    @staticmethod
    def _record(
        meta: LogFileMeta,
        line_no: int,
        raw_ts: Optional[float],
        aligned_ts: Optional[float],
        text: str,
        clock_unaligned: bool = False,
    ) -> dict:
        out: dict = {
            "controller": meta.controller.value,
            "file_id": str(meta.file_id),
            "aligned_ts": aligned_ts,
            "raw_ts": raw_ts,
            "line_no": line_no,
            "line": text,
        }
        if clock_unaligned:
            out["clock_unaligned"] = True
        return out


def estimate_total_lines(catalog: Catalog, params: RangeQueryParams) -> int:
    """Cheap upper-bound estimate for X-Truncated header — sums file line counts
    of overlapping files."""
    files = catalog.list_files_by_bundle(params.bundle_id)
    if params.controllers:
        allow = set(params.controllers)
        files = [f for f in files if f.controller in allow]
    total = 0
    for f in files:
        if not f.decoded_path:
            continue
        if _file_overlaps(f, params.start, params.end):
            total += f.line_count
        elif (
            params.include_unsynced
            and f.clock_offset is None
            and f.offset_method != AlignmentMethod.SEGMENTED
        ):
            total += f.line_count
    return total
