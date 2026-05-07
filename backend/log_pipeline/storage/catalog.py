from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional
from uuid import UUID

from log_pipeline.interfaces import (
    INVALID_TS_SENTINEL_2020_END,
    MIN_VALID_TS,
    AlignmentMethod,
    BootSegment,
    BundleStatus,
    ControllerType,
    LogFileMeta,
)

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS bundles (
      bundle_id              TEXT PRIMARY KEY,
      status                 TEXT NOT NULL,
      progress               REAL NOT NULL DEFAULT 0,
      archive_filename       TEXT,
      archive_size_bytes     INTEGER,
      error                  TEXT,
      alignment_summary_json TEXT,
      created_at             TEXT NOT NULL,
      updated_at             TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog (
      file_id              TEXT PRIMARY KEY,
      bundle_id            TEXT NOT NULL,
      controller           TEXT NOT NULL,
      original_name        TEXT NOT NULL,
      bundle_relative_path TEXT NOT NULL,
      size_bytes           INTEGER NOT NULL DEFAULT 0,
      sha256               TEXT,
      stored_path          TEXT NOT NULL,
      decoded_path         TEXT,
      valid_ts_min         REAL,
      valid_ts_max         REAL,
      raw_ts_min           REAL,
      raw_ts_max           REAL,
      clock_offset         REAL,
      offset_confidence    REAL NOT NULL DEFAULT 0,
      offset_method        TEXT NOT NULL DEFAULT 'none',
      line_count           INTEGER NOT NULL DEFAULT 0,
      bucket_index_path    TEXT,
      unsynced_ranges_json TEXT NOT NULL DEFAULT '[]',
      segments_json        TEXT NOT NULL DEFAULT '[]',
      created_at           TEXT NOT NULL,
      FOREIGN KEY (bundle_id) REFERENCES bundles(bundle_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_catalog_bundle_ctrl ON catalog(bundle_id, controller);",
    "CREATE INDEX IF NOT EXISTS idx_catalog_time ON catalog(bundle_id, valid_ts_min, valid_ts_max);",
    # idx_catalog_bundle_hash supports the per-bundle dedup lookup before insert.
    "CREATE INDEX IF NOT EXISTS idx_catalog_bundle_hash ON catalog(bundle_id, sha256);",
]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Catalog:
    """Thin DAO over SQLite for bundle and file metadata.

    Connections are per-thread to keep sqlite3 happy; a single Catalog instance
    is safe to share across FastAPI threads.
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._tls = threading.local()
        self._ensure_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._tls, "conn", None)
        if c is None:
            c = sqlite3.connect(
                self._db_path,
                detect_types=sqlite3.PARSE_DECLTYPES,
                isolation_level=None,
            )
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute("PRAGMA foreign_keys=ON;")
            c.row_factory = sqlite3.Row
            self._tls.conn = c
        return c

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._conn()
        conn.execute("BEGIN")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def _ensure_schema(self) -> None:
        conn = self._conn()
        for stmt in _SCHEMA:
            conn.execute(stmt)

    # --- bundles ---

    def create_bundle(
        self,
        bundle_id: UUID,
        archive_filename: Optional[str],
        archive_size_bytes: Optional[int],
        status: BundleStatus = BundleStatus.QUEUED,
    ) -> None:
        now = _iso_now()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO bundles (bundle_id, status, archive_filename, "
                "archive_size_bytes, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (str(bundle_id), status.value, archive_filename, archive_size_bytes, now, now),
            )

    def update_bundle_status(
        self,
        bundle_id: UUID,
        status: BundleStatus,
        progress: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        sets = ["status = ?", "updated_at = ?"]
        args: list = [status.value, _iso_now()]
        if progress is not None:
            sets.append("progress = ?")
            args.append(progress)
        if error is not None:
            sets.append("error = ?")
            args.append(error)
        args.append(str(bundle_id))
        with self._tx() as conn:
            conn.execute(f"UPDATE bundles SET {', '.join(sets)} WHERE bundle_id = ?", args)

    def get_bundle(self, bundle_id: UUID) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM bundles WHERE bundle_id = ?", (str(bundle_id),)
        ).fetchone()
        return dict(row) if row else None

    # --- catalog (files) ---

    def insert_file_meta(self, meta: LogFileMeta) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO catalog (file_id, bundle_id, controller, original_name, "
                "bundle_relative_path, size_bytes, sha256, stored_path, decoded_path, "
                "valid_ts_min, valid_ts_max, raw_ts_min, raw_ts_max, clock_offset, "
                "offset_confidence, offset_method, line_count, bucket_index_path, "
                "unsynced_ranges_json, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(meta.file_id),
                    str(meta.bundle_id),
                    meta.controller.value,
                    meta.original_name,
                    meta.bundle_relative_path,
                    meta.size_bytes,
                    meta.sha256,
                    meta.stored_path,
                    meta.decoded_path,
                    meta.valid_ts_min,
                    meta.valid_ts_max,
                    meta.raw_ts_min,
                    meta.raw_ts_max,
                    meta.clock_offset,
                    meta.offset_confidence,
                    meta.offset_method.value,
                    meta.line_count,
                    meta.bucket_index_path,
                    json.dumps(list(meta.unsynced_line_ranges)),
                    _iso_now(),
                ),
            )

    def file_id_by_hash(self, bundle_id: UUID, sha256: str) -> Optional[UUID]:
        """Return the existing file_id for ``(bundle, sha256)`` or None."""
        row = self._conn().execute(
            "SELECT file_id FROM catalog WHERE bundle_id = ? AND sha256 = ? LIMIT 1",
            (str(bundle_id), sha256),
        ).fetchone()
        return UUID(row["file_id"]) if row else None

    def update_file_clock_offset(
        self,
        file_id: UUID,
        clock_offset: Optional[float],
        offset_confidence: float,
        offset_method: str,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE catalog SET clock_offset = ?, offset_confidence = ?, "
                "offset_method = ? WHERE file_id = ?",
                (clock_offset, offset_confidence, offset_method, str(file_id)),
            )

    def update_file_segments(
        self,
        file_id: UUID,
        segments: list[BootSegment],
        offset_method: str = "segmented",
    ) -> None:
        """Persist per-boot segments and switch the file to SEGMENTED method.
        Per-segment ``clock_offset`` is authoritative; the file-level
        ``clock_offset`` is left NULL so the query path knows to consult
        segments instead."""
        payload = json.dumps(
            [
                {
                    "seq_no": s.seq_no,
                    "line_start": s.line_start,
                    "line_end": s.line_end,
                    "byte_start": s.byte_start,
                    "byte_end": s.byte_end,
                    "raw_ts_min": s.raw_ts_min,
                    "raw_ts_max": s.raw_ts_max,
                    "clock_offset": s.clock_offset,
                    "offset_confidence": s.offset_confidence,
                }
                for s in segments
            ]
        )
        with self._tx() as conn:
            conn.execute(
                "UPDATE catalog SET segments_json = ?, "
                "offset_method = ?, clock_offset = NULL "
                "WHERE file_id = ?",
                (payload, offset_method, str(file_id)),
            )

    def set_bundle_alignment_summary(self, bundle_id: UUID, summary_json: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE bundles SET alignment_summary_json = ?, updated_at = ? WHERE bundle_id = ?",
                (summary_json, _iso_now(), str(bundle_id)),
            )

    def update_file_prescan_meta(
        self,
        file_id: UUID,
        bucket_index_path: Optional[str],
        line_count: int,
        raw_ts_min: Optional[float],
        raw_ts_max: Optional[float],
        valid_ts_min: Optional[float],
        valid_ts_max: Optional[float],
        unsynced_line_ranges: list[tuple[int, int]],
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE catalog SET bucket_index_path = ?, line_count = ?, "
                "raw_ts_min = ?, raw_ts_max = ?, valid_ts_min = ?, valid_ts_max = ?, "
                "unsynced_ranges_json = ? WHERE file_id = ?",
                (
                    bucket_index_path,
                    line_count,
                    raw_ts_min,
                    raw_ts_max,
                    valid_ts_min,
                    valid_ts_max,
                    json.dumps(list(unsynced_line_ranges)),
                    str(file_id),
                ),
            )

    def update_file_decoded_meta(
        self,
        file_id: UUID,
        decoded_path: Optional[str],
        line_count: int,
        raw_ts_min: Optional[float],
        raw_ts_max: Optional[float],
        valid_ts_min: Optional[float] = None,
        valid_ts_max: Optional[float] = None,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE catalog SET decoded_path = ?, line_count = ?, "
                "raw_ts_min = ?, raw_ts_max = ?, "
                "valid_ts_min = COALESCE(?, valid_ts_min), "
                "valid_ts_max = COALESCE(?, valid_ts_max) "
                "WHERE file_id = ?",
                (
                    decoded_path,
                    line_count,
                    raw_ts_min,
                    raw_ts_max,
                    valid_ts_min,
                    valid_ts_max,
                    str(file_id),
                ),
            )

    def list_files_by_bundle(self, bundle_id: UUID) -> list[LogFileMeta]:
        rows = self._conn().execute(
            "SELECT * FROM catalog WHERE bundle_id = ? ORDER BY controller, original_name",
            (str(bundle_id),),
        ).fetchall()
        return [self._row_to_meta(r) for r in rows]

    def count_bundles_by_status(self) -> dict[str, int]:
        rows = self._conn().execute(
            "SELECT status, COUNT(*) AS n FROM bundles GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def count_files_by_controller_global(self) -> dict[str, int]:
        rows = self._conn().execute(
            "SELECT controller, COUNT(*) AS n FROM catalog GROUP BY controller"
        ).fetchall()
        return {r["controller"]: r["n"] for r in rows}

    def list_latest_offsets(self) -> list[tuple[str, Optional[float], float]]:
        """Return one (controller, offset, confidence) row per controller, taken
        from the most recently created bundle that has any offset for that
        controller. Used for /metrics."""
        rows = self._conn().execute(
            """
            SELECT controller,
                   AVG(clock_offset)      AS offset,
                   AVG(offset_confidence) AS confidence
              FROM catalog
              JOIN bundles USING (bundle_id)
             WHERE bundles.bundle_id = (
                     SELECT bundle_id FROM bundles
                      WHERE status = 'done' ORDER BY created_at DESC LIMIT 1
                   )
             GROUP BY controller
            """
        ).fetchall()
        return [(r["controller"], r["offset"], r["confidence"] or 0.0) for r in rows]

    def count_by_controller(self, bundle_id: UUID) -> dict[str, int]:
        rows = self._conn().execute(
            "SELECT controller, COUNT(*) AS n FROM catalog WHERE bundle_id = ? GROUP BY controller",
            (str(bundle_id),),
        ).fetchall()
        return {r["controller"]: r["n"] for r in rows}

    def valid_time_range_by_controller(
        self, bundle_id: UUID
    ) -> dict[str, dict[str, Optional[float]]]:
        """Aggregate per-controller valid aligned timestamp range within one bundle.

        SEGMENTED files persist ``valid_ts_*`` already in aligned space.
        Non-segmented files persist raw-space ``valid_ts_*`` and require
        ``clock_offset`` shift here to compare on the same wall-clock axis.
        """
        rows = self._conn().execute(
            """
            WITH aligned AS (
              SELECT controller,
                     CASE
                       WHEN offset_method = 'segmented' THEN valid_ts_min
                       WHEN clock_offset IS NOT NULL THEN valid_ts_min + clock_offset
                       ELSE valid_ts_min
                     END AS aligned_min,
                     CASE
                       WHEN offset_method = 'segmented' THEN valid_ts_max
                       WHEN clock_offset IS NOT NULL THEN valid_ts_max + clock_offset
                       ELSE valid_ts_max
                     END AS aligned_max
                FROM catalog
               WHERE bundle_id = ?
                 AND valid_ts_min IS NOT NULL
                 AND valid_ts_max IS NOT NULL
            )
            SELECT controller,
                   MIN(aligned_min) AS valid_start,
                   MAX(aligned_max) AS valid_end
              FROM aligned
             WHERE aligned_min IS NOT NULL
               AND aligned_max IS NOT NULL
               AND aligned_min >= ?
               AND aligned_max >= ?
               AND NOT (aligned_min >= ? AND aligned_min < ?)
               AND NOT (aligned_max >= ? AND aligned_max < ?)
             GROUP BY controller
            """,
            (
                str(bundle_id),
                MIN_VALID_TS,
                MIN_VALID_TS,
                MIN_VALID_TS,
                INVALID_TS_SENTINEL_2020_END,
                MIN_VALID_TS,
                INVALID_TS_SENTINEL_2020_END,
            ),
        ).fetchall()
        return {
            r["controller"]: {"start": r["valid_start"], "end": r["valid_end"]}
            for r in rows
        }

    @staticmethod
    def _row_to_meta(row: sqlite3.Row) -> LogFileMeta:
        ranges_raw = json.loads(row["unsynced_ranges_json"] or "[]")
        ranges = tuple((int(a), int(b)) for a, b in ranges_raw)
        # ``segments_json`` was added late — older rows may lack the column or
        # leave it as the empty default. Tolerate both by using row.keys().
        seg_raw = "[]"
        try:
            seg_raw = row["segments_json"] or "[]"
        except (IndexError, KeyError):
            seg_raw = "[]"
        seg_list = json.loads(seg_raw)
        segments = tuple(
            BootSegment(
                seq_no=int(s["seq_no"]),
                line_start=int(s["line_start"]),
                line_end=int(s["line_end"]),
                byte_start=int(s["byte_start"]),
                byte_end=int(s["byte_end"]),
                raw_ts_min=s.get("raw_ts_min"),
                raw_ts_max=s.get("raw_ts_max"),
                clock_offset=s.get("clock_offset"),
                offset_confidence=float(s.get("offset_confidence", 0.0)),
            )
            for s in seg_list
        )
        try:
            sha = row["sha256"]
        except (IndexError, KeyError):
            sha = None
        return LogFileMeta(
            file_id=UUID(row["file_id"]),
            bundle_id=UUID(row["bundle_id"]),
            controller=ControllerType(row["controller"]),
            original_name=row["original_name"],
            stored_path=row["stored_path"],
            bundle_relative_path=row["bundle_relative_path"] or "",
            size_bytes=row["size_bytes"] or 0,
            sha256=sha,
            decoded_path=row["decoded_path"],
            raw_ts_min=row["raw_ts_min"],
            raw_ts_max=row["raw_ts_max"],
            valid_ts_min=row["valid_ts_min"],
            valid_ts_max=row["valid_ts_max"],
            unsynced_line_ranges=ranges,
            line_count=row["line_count"] or 0,
            bucket_index_path=row["bucket_index_path"],
            clock_offset=row["clock_offset"],
            offset_confidence=row["offset_confidence"] or 0.0,
            offset_method=AlignmentMethod(row["offset_method"]),
            segments=segments,
        )
