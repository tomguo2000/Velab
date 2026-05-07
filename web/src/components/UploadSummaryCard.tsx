"use client";

import { MouseEvent as ReactMouseEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EventDigest, EventDigestItem, UploadSummary } from "@/lib/types";

interface UploadSummaryCardProps {
  summary: UploadSummary;
}

// ---- EventDigestPanel ----

const EVENT_TYPE_LABELS: Record<string, string> = {
  system_reboot: "系统重启",
  panic_or_fatal: "内核崩溃/致命异常",
  kernel_oops_or_bug: "Kernel Oops/BUG",
  kernel_watchdog: "看门狗复位",
  fota_install_success: "FOTA 安装成功",
  fota_install_failure: "FOTA 安装失败",
  fota_download_start: "FOTA 开始下载",
  fota_install_start: "FOTA 开始安装",
};

function formatTs(ts: number | undefined, formatter: Intl.DateTimeFormat): string {
  if (typeof ts !== "number" || Number.isNaN(ts)) return "—";
  return formatter.format(new Date(ts * 1000));
}

function DigestRow({
  icon,
  label,
  item,
  color,
  formatter,
}: {
  icon: string;
  label: string;
  item: EventDigestItem;
  color: string;
  formatter: Intl.DateTimeFormat;
}) {
  const typeLabel = EVENT_TYPE_LABELS[item.eventType] ?? item.eventType;
  return (
    <div className="flex items-start gap-2 text-xs">
      <span className="mt-0.5 flex-shrink-0">{icon}</span>
      <div className="min-w-0">
        <span className="font-medium" style={{ color }}>
          {label}
        </span>
        <span className="ml-1" style={{ color: "var(--text-primary)" }}>
          {typeLabel}
        </span>
        <span className="ml-1" style={{ color: "var(--text-secondary)" }}>
          [{item.controller}]
        </span>
        <span className="ml-1" style={{ color: "var(--text-muted)" }}>
          {formatTs(item.timestamp, formatter)}
        </span>
        {item.rawLine ? (
          <div
            className="mt-0.5 truncate rounded px-1.5 py-0.5 font-mono text-[10px]"
            style={{ background: "var(--border-light)", color: "var(--text-secondary)" }}
            title={item.rawLine}
          >
            {item.rawLine}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function EventDigestPanel({
  digest,
  formatter,
}: {
  digest: EventDigest;
  formatter: Intl.DateTimeFormat;
}) {
  const hasFault = Boolean(digest.lastCriticalFault);
  const hasFota = Boolean(digest.fotaResult);
  const hasReboot = Boolean(digest.lastReboot);

  if (!hasReboot && !hasFault && !hasFota && digest.totalEvents === 0) return null;

  return (
    <div
      className="mt-2 rounded-lg border p-2.5 grid gap-2"
      style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold" style={{ color: "var(--text-secondary)" }}>
          事件摘要
        </span>
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          共 {digest.totalEvents} 个事件
          {digest.criticalCount > 0 && (
            <span className="ml-1" style={{ color: "#f85149" }}>
              · {digest.criticalCount} 个严重异常
            </span>
          )}
        </span>
      </div>

      {digest.lastReboot && (
        <DigestRow
          icon="🔄"
          label="最近重启"
          item={digest.lastReboot}
          color="var(--text-primary)"
          formatter={formatter}
        />
      )}

      {digest.lastCriticalFault && (
        <DigestRow
          icon="🔴"
          label="最后故障"
          item={digest.lastCriticalFault}
          color="#f85149"
          formatter={formatter}
        />
      )}

      {digest.fotaResult && (
        <DigestRow
          icon={digest.fotaResult.success ? "✅" : "❌"}
          label={digest.fotaResult.success ? "FOTA 结果" : "FOTA 结果"}
          item={digest.fotaResult}
          color={digest.fotaResult.success ? "#3fb950" : "#f85149"}
          formatter={formatter}
        />
      )}

      {!hasReboot && !hasFault && !hasFota && digest.totalEvents > 0 && (
        <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          已解析 {digest.totalEvents} 个事件（无重启/故障/FOTA 记录）
        </div>
      )}
    </div>
  );
}

type Lane = {
  controller: string;
  count: number;
  start?: number;
  end?: number;
};

type EventItem = {
  id: string;
  controller: string;
  eventType: string;
  timestamp: number;
};

type BrushRange = {
  start: number;
  end: number;
};
type TimeDisplayMode = "absolute" | "relative";

type BrushDragMode = "new" | "moveStart" | "moveEnd" | "moveRange" | null;

type BrushDragState = {
  anchorTs: number;
  initialRange: BrushRange;
};

const toEventItems = (payload: unknown): EventItem[] => {
  if (!Array.isArray(payload)) return [];
  return payload
    .map((raw): EventItem | null => {
      if (!raw || typeof raw !== "object") return null;
      const candidate = raw as Record<string, unknown>;
      const aligned = candidate.aligned_timestamp;
      const fallback = candidate.raw_timestamp;
      const timestamp = typeof aligned === "number" ? aligned : (typeof fallback === "number" ? fallback : NaN);
      const controller = typeof candidate.controller === "string" ? candidate.controller : "";
      const eventType = typeof candidate.event_type === "string" ? candidate.event_type : "event";
      const eventId = typeof candidate.event_id === "string" ? candidate.event_id : `${controller}-${eventType}-${timestamp}`;
      if (!controller || Number.isNaN(timestamp)) return null;
      return { id: eventId, controller, eventType, timestamp };
    })
    .filter((item): item is EventItem => Boolean(item));
};

export default function UploadSummaryCard({ summary }: UploadSummaryCardProps) {
  const [brushRange, setBrushRange] = useState<BrushRange | null>(null);
  const [brushDragMode, setBrushDragMode] = useState<BrushDragMode>(null);
  const [timeDisplayMode, setTimeDisplayMode] = useState<TimeDisplayMode>("absolute");
  const [events, setEvents] = useState<EventItem[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const brushTrackRef = useRef<HTMLDivElement | null>(null);
  const brushDragStateRef = useRef<BrushDragState | null>(null);

  const lanes: Lane[] = Object.entries(summary.filesByController).map(([controller, count]) => ({
    controller,
    count,
    start: summary.validTimeRangeByController[controller]?.start,
    end: summary.validTimeRangeByController[controller]?.end,
  }));

  const validRanges = lanes.filter((lane) => typeof lane.start === "number" && typeof lane.end === "number");
  const globalStart = validRanges.length > 0 ? Math.min(...validRanges.map((lane) => lane.start as number)) : undefined;
  const globalEnd = validRanges.length > 0 ? Math.max(...validRanges.map((lane) => lane.end as number)) : undefined;
  const totalSpan = typeof globalStart === "number" && typeof globalEnd === "number" && globalEnd > globalStart
    ? globalEnd - globalStart
    : 0;
  const windowStart = brushRange?.start ?? globalStart;
  const windowEnd = brushRange?.end ?? globalEnd;
  const windowSpan = typeof windowStart === "number" && typeof windowEnd === "number" && windowEnd > windowStart
    ? windowEnd - windowStart
    : totalSpan;
  const timeZone = useMemo(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "本地时区",
    []
  );
  const localDateTimeFormatter = useMemo(
    () =>
      new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }),
    []
  );
  const tickFormatter = useMemo(
    () =>
      new Intl.DateTimeFormat(undefined, {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }),
    []
  );
  const timeOnlyFormatter = useMemo(
    () =>
      new Intl.DateTimeFormat(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }),
    []
  );
  const monthDayTimeFormatter = useMemo(
    () =>
      new Intl.DateTimeFormat(undefined, {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }),
    []
  );
  const toLocalDateTime = (value?: number): string => {
    if (typeof value !== "number" || Number.isNaN(value)) return "无有效时间";
    return localDateTimeFormatter.format(new Date(value * 1000));
  };
  const toDurationLabel = (seconds?: number): string => {
    if (typeof seconds !== "number" || Number.isNaN(seconds) || seconds < 0) return "";
    const total = Math.round(seconds);
    if (total < 60) return `${total}s`;
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (hours === 0) return `${minutes}m`;
    if (minutes === 0) return `${hours}h`;
    return `${hours}h ${minutes}m`;
  };
  const toDeltaLabel = (seconds?: number): string => {
    if (typeof seconds !== "number" || Number.isNaN(seconds)) return "Δ--";
    const rounded = Math.max(0, Math.round(seconds));
    const hours = Math.floor(rounded / 3600);
    const minutes = Math.floor((rounded % 3600) / 60);
    const secs = rounded % 60;
    if (hours > 0) return `Δ${hours}h ${minutes}m`;
    if (minutes > 0) return `Δ${minutes}m ${secs}s`;
    return `Δ${secs}s`;
  };
  const toLaneRangeLabel = (start?: number, end?: number): string => {
    if (
      typeof start !== "number"
      || Number.isNaN(start)
      || typeof end !== "number"
      || Number.isNaN(end)
      || end < start
    ) {
      return "无有效时间";
    }
    const startDate = new Date(start * 1000);
    const endDate = new Date(end * 1000);
    const sameDay = startDate.getFullYear() === endDate.getFullYear()
      && startDate.getMonth() === endDate.getMonth()
      && startDate.getDate() === endDate.getDate();
    const startLabel = sameDay
      ? timeOnlyFormatter.format(startDate)
      : monthDayTimeFormatter.format(startDate);
    const endLabel = sameDay
      ? timeOnlyFormatter.format(endDate)
      : monthDayTimeFormatter.format(endDate);
    const duration = toDurationLabel(end - start);
    return duration ? `${startLabel} - ${endLabel} · ${duration}` : `${startLabel} - ${endLabel}`;
  };
  const toLaneRelativeLabel = (
    start?: number,
    end?: number,
    referenceStart?: number
  ): string => {
    if (
      typeof start !== "number"
      || Number.isNaN(start)
      || typeof end !== "number"
      || Number.isNaN(end)
      || end < start
      || typeof referenceStart !== "number"
      || Number.isNaN(referenceStart)
    ) {
      return "无有效时间";
    }
    const startDelta = toDeltaLabel(start - referenceStart);
    const endDelta = toDeltaLabel(end - referenceStart);
    const duration = toDurationLabel(end - start);
    return duration ? `${startDelta} - ${endDelta} · ${duration}` : `${startDelta} - ${endDelta}`;
  };
  const formatTick = useCallback(
    (value: number): string => tickFormatter.format(new Date(value * 1000)),
    [tickFormatter]
  );

  const ticks = useMemo(() => {
    if (typeof windowStart !== "number" || typeof windowEnd !== "number" || windowSpan <= 0) return [];
    return Array.from({ length: 5 }, (_, i) => {
      const ratio = i / 4;
      const ts = windowStart + ratio * windowSpan;
      return {
        key: `${ratio}`,
        label: formatTick(ts),
        left: `${ratio * 100}%`,
      };
    });
  }, [formatTick, windowEnd, windowSpan, windowStart]);
  const minBrushSpan = useMemo(() => Math.max(1, totalSpan * 0.01), [totalSpan]);

  const clampTime = useCallback((value: number): number => {
    if (typeof globalStart !== "number" || typeof globalEnd !== "number") return value;
    return Math.max(globalStart, Math.min(globalEnd, value));
  }, [globalEnd, globalStart]);

  const toPercentFromTime = (value: number): number => {
    if (typeof globalStart !== "number" || totalSpan <= 0) return 0;
    return Math.max(0, Math.min(100, ((value - globalStart) / totalSpan) * 100));
  };

  const getTimeFromClientX = useCallback((clientX: number): number | null => {
    if (typeof globalStart !== "number" || totalSpan <= 0) return null;
    const track = brushTrackRef.current;
    if (!track) return null;
    const rect = track.getBoundingClientRect();
    if (rect.width <= 0) return null;
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return globalStart + ratio * totalSpan;
  }, [globalStart, totalSpan]);

  const normalizeRange = useCallback((start: number, end: number): BrushRange => {
    let nextStart = clampTime(Math.min(start, end));
    let nextEnd = clampTime(Math.max(start, end));
    if (nextEnd - nextStart < minBrushSpan) {
      nextEnd = clampTime(nextStart + minBrushSpan);
      if (nextEnd - nextStart < minBrushSpan) {
        nextStart = clampTime(nextEnd - minBrushSpan);
      }
    }
    return { start: nextStart, end: nextEnd };
  }, [clampTime, minBrushSpan]);

  const startBrushDrag = (mode: Exclude<BrushDragMode, null>, event: ReactMouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const currentTs = getTimeFromClientX(event.clientX);
    if (currentTs === null) return;
    const initialRange = brushRange ?? { start: currentTs, end: currentTs };
    brushDragStateRef.current = { anchorTs: currentTs, initialRange };
    if (mode === "new") {
      setBrushRange(normalizeRange(currentTs, currentTs + minBrushSpan));
    }
    setBrushDragMode(mode);
  };

  useEffect(() => {
    if (!brushDragMode) return;

    const onMove = (event: MouseEvent) => {
      const currentTs = getTimeFromClientX(event.clientX);
      const dragState = brushDragStateRef.current;
      if (currentTs === null || !dragState) return;
      const currentRange = brushRange ?? dragState.initialRange;
      if (brushDragMode === "new") {
        setBrushRange(normalizeRange(dragState.anchorTs, currentTs));
        return;
      }
      if (brushDragMode === "moveStart") {
        setBrushRange(normalizeRange(currentTs, currentRange.end));
        return;
      }
      if (brushDragMode === "moveEnd") {
        setBrushRange(normalizeRange(currentRange.start, currentTs));
        return;
      }
      if (brushDragMode === "moveRange") {
        const delta = currentTs - dragState.anchorTs;
        const span = dragState.initialRange.end - dragState.initialRange.start;
        let nextStart = dragState.initialRange.start + delta;
        let nextEnd = dragState.initialRange.end + delta;
        if (typeof globalStart === "number" && nextStart < globalStart) {
          nextStart = globalStart;
          nextEnd = globalStart + span;
        }
        if (typeof globalEnd === "number" && nextEnd > globalEnd) {
          nextEnd = globalEnd;
          nextStart = globalEnd - span;
        }
        setBrushRange(normalizeRange(nextStart, nextEnd));
      }
    };

    const onUp = () => {
      setBrushDragMode(null);
      brushDragStateRef.current = null;
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [brushDragMode, brushRange, getTimeFromClientX, globalEnd, globalStart, normalizeRange]);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setEventsLoading(true);
      setEventsError(null);
      try {
        const resp = await fetch(`/api/bundle-events/${encodeURIComponent(summary.bundleId)}`);
        const payload = await resp.json();
        if (!resp.ok) {
          const detail = typeof payload?.detail === "string"
            ? payload.detail
            : (typeof payload?.error?.message === "string" ? payload.error.message : resp.status);
          throw new Error(String(detail));
        }
        if (cancelled) return;
        setEvents(toEventItems(payload));
      } catch (err) {
        if (cancelled) return;
        setEventsError((err as Error).message);
      } finally {
        if (!cancelled) setEventsLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [summary.bundleId]);

  const visibleEvents = useMemo(() => {
    if (typeof windowStart !== "number" || typeof windowEnd !== "number") return [];
    return events.filter((event) => event.timestamp >= windowStart && event.timestamp <= windowEnd);
  }, [events, windowEnd, windowStart]);

  const groupedEventsByController = useMemo(() => {
    if (windowSpan <= 0 || typeof windowStart !== "number") return new Map<string, Array<{ leftPct: number; count: number; title: string }>>();
    const groupMap = new Map<string, Record<number, EventItem[]>>();
    visibleEvents.forEach((event) => {
      const normalizedLeft = ((event.timestamp - windowStart) / windowSpan) * 100;
      const clampedLeft = Math.max(0, Math.min(100, normalizedLeft));
      const bucket = Math.floor(clampedLeft / 3);
      const byBucket = groupMap.get(event.controller) ?? {};
      byBucket[bucket] = [...(byBucket[bucket] ?? []), event];
      groupMap.set(event.controller, byBucket);
    });
    const flattened = new Map<string, Array<{ leftPct: number; count: number; title: string }>>();
    groupMap.forEach((buckets, controller) => {
      const points = Object.entries(buckets).map(([bucketKey, items]) => {
        const bucket = Number(bucketKey);
        const leftPct = Math.max(0, Math.min(100, bucket * 3 + 1.5));
        const headline = items.slice(0, 3).map((item) => item.eventType).join(", ");
        const tail = items.length > 3 ? ` +${items.length - 3}` : "";
        return {
          leftPct,
          count: items.length,
          title: `${controller} 事件 (${items.length})：${headline}${tail}`,
        };
      });
      flattened.set(controller, points);
    });
    return flattened;
  }, [visibleEvents, windowSpan, windowStart]);

  return (
    <section
      className="mt-3 rounded-xl border p-3"
      style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}
    >
      <div className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
        上传 Summary · {summary.fileName}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs" style={{ color: "var(--text-secondary)" }}>
        <span>共 {summary.fileCount} 个文件</span>
        <span>{Object.keys(summary.filesByController).length} 类日志</span>
        <span>时区：{timeZone}</span>
        <span>
          事件 {eventsLoading ? "加载中..." : `${visibleEvents.length}/${events.length}`}
        </span>
      </div>

      {summary.eventDigest && <EventDigestPanel digest={summary.eventDigest} formatter={localDateTimeFormatter} />}

      <div className="mt-2 rounded-lg border p-2" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
        <div className="mb-1 flex items-center justify-between text-[11px]" style={{ color: "var(--text-secondary)" }}>
          <span>拖拽时间轴以 brush 缩放</span>
          {brushRange ? (
            <button
              type="button"
              onClick={() => setBrushRange(null)}
              className="rounded px-2 py-0.5"
              style={{ border: "1px solid var(--border-color)" }}
            >
              重置
            </button>
          ) : null}
        </div>
        <div
          ref={brushTrackRef}
          data-testid="summary-brush-track"
          className="relative h-8 cursor-crosshair rounded"
          style={{ background: "var(--border-light)" }}
          onMouseDown={(event) => startBrushDrag("new", event)}
        >
          {brushRange ? (
            <div
              data-testid="summary-brush-selection"
              className="absolute top-0 h-8 cursor-move rounded"
              style={{
                left: `${toPercentFromTime(brushRange.start)}%`,
                width: `${Math.max(2, toPercentFromTime(brushRange.end) - toPercentFromTime(brushRange.start))}%`,
                background: "rgba(56, 139, 253, 0.28)",
                border: "1px solid var(--accent-blue)",
              }}
              onMouseDown={(event) => startBrushDrag("moveRange", event)}
            >
              <div
                className="absolute left-0 top-0 h-full w-1.5 cursor-ew-resize"
                style={{ background: "var(--accent-blue)" }}
                onMouseDown={(event) => startBrushDrag("moveStart", event)}
              />
              <div
                className="absolute right-0 top-0 h-full w-1.5 cursor-ew-resize"
                style={{ background: "var(--accent-blue)" }}
                onMouseDown={(event) => startBrushDrag("moveEnd", event)}
              />
            </div>
          ) : null}
        </div>
      </div>

      <div className="mt-3 text-xs" style={{ color: "var(--text-secondary)" }}>
        时间窗口：{toLocalDateTime(windowStart)} ~ {toLocalDateTime(windowEnd)}
      </div>
      <div className="mt-2 flex items-center gap-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <span>时间显示：</span>
        <button
          type="button"
          className="rounded px-2 py-0.5"
          style={{
            border: "1px solid var(--border-color)",
            background: timeDisplayMode === "absolute" ? "var(--accent-blue)" : "var(--bg-primary)",
            color: timeDisplayMode === "absolute" ? "#fff" : "var(--text-secondary)",
          }}
          onClick={() => setTimeDisplayMode("absolute")}
        >
          绝对时间
        </button>
        <button
          type="button"
          className="rounded px-2 py-0.5"
          style={{
            border: "1px solid var(--border-color)",
            background: timeDisplayMode === "relative" ? "var(--accent-blue)" : "var(--bg-primary)",
            color: timeDisplayMode === "relative" ? "#fff" : "var(--text-secondary)",
          }}
          onClick={() => setTimeDisplayMode("relative")}
        >
          相对时间(Δt)
        </button>
      </div>

      <div className="mt-3">
        <div className="relative h-5">
          {ticks.map((tick) => (
            <div key={tick.key} className="absolute top-0 -translate-x-1/2 text-[10px]" style={{ left: tick.left, color: "var(--text-muted)" }}>
              {tick.label}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-2 grid gap-2">
        {lanes.map((lane, idx) => {
          const laneStart = typeof lane.start === "number" ? Math.max(lane.start, windowStart ?? lane.start) : undefined;
          const laneEnd = typeof lane.end === "number" ? Math.min(lane.end, windowEnd ?? lane.end) : undefined;
          const hasRange = windowSpan > 0
            && typeof laneStart === "number"
            && typeof laneEnd === "number"
            && laneEnd >= laneStart;
          const leftPct = hasRange ? (((laneStart as number) - (windowStart as number)) / windowSpan) * 100 : 0;
          const widthPct = hasRange ? (((laneEnd as number) - (laneStart as number)) / windowSpan) * 100 : 0;
          const eventPoints = groupedEventsByController.get(lane.controller) ?? [];
          return (
            <div key={lane.controller} className="grid gap-1">
              <div className="flex items-center justify-between text-xs">
                <span style={{ color: "var(--text-primary)" }}>
                  {lane.controller} ({lane.count})
                </span>
                <span style={{ color: "var(--text-secondary)" }}>
                  {timeDisplayMode === "relative"
                    ? toLaneRelativeLabel(lane.start, lane.end, windowStart)
                    : toLaneRangeLabel(lane.start, lane.end)}
                </span>
              </div>
              <div className="relative h-4 rounded-full" style={{ background: "var(--border-light)" }}>
                {hasRange ? (
                  <div
                    className="absolute top-0 h-4 rounded-full"
                    style={{
                      left: `${Math.max(0, Math.min(100, leftPct))}%`,
                      width: `${Math.max(2, Math.min(100, widthPct || 2))}%`,
                      background: idx % 2 === 0 ? "var(--accent-blue)" : "#a371f7",
                    }}
                    title={`${lane.controller}: ${toLocalDateTime(lane.start)} ~ ${toLocalDateTime(lane.end)} (${timeZone})`}
                  />
                ) : (
                  <div className="absolute inset-0 flex items-center justify-center text-[10px]" style={{ color: "var(--text-muted)" }}>
                    无有效时间
                  </div>
                )}
                {eventPoints.map((point) => (
                  <div
                    key={`${lane.controller}-${point.leftPct}-${point.count}`}
                    className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 rounded-full border"
                    style={{
                      left: `${point.leftPct}%`,
                      minWidth: point.count > 1 ? 18 : 8,
                      height: point.count > 1 ? 16 : 8,
                      padding: point.count > 1 ? "0 4px" : 0,
                      background: point.count > 1 ? "var(--bg-primary)" : "#f85149",
                      borderColor: "#f85149",
                      color: point.count > 1 ? "#f85149" : "transparent",
                      fontSize: "10px",
                      lineHeight: point.count > 1 ? "14px" : "8px",
                      textAlign: "center",
                      fontWeight: 600,
                    }}
                    title={point.title}
                  >
                    {point.count > 1 ? `+${point.count}` : "1"}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      {eventsError ? (
        <div className="mt-2 text-[11px]" style={{ color: "#f85149" }}>
          事件加载失败：{eventsError}
        </div>
      ) : null}
    </section>
  );
}
