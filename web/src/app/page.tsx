/**
 * FOTA 诊断平台 — 主页面组件
 *
 * 这是应用的核心页面，实现了完整的诊断对话流程：
 * 1. 场景选择：支持多种诊断场景切换
 * 2. 消息管理：维护用户和助手的对话历史
 * 3. SSE 流式处理：实时接收和展示诊断过程
 * 4. 状态管理：处理加载、流式输出、错误等状态
 *
 * 主要功能：
 * - 实时流式显示 Agent 执行过程（Thinking Process）
 * - 支持中断正在进行的诊断
 * - 自动滚动到最新消息
 * - 场景切换时清空对话历史
 *
 * @author FOTA 诊断平台团队
 * @created 2025
 * @updated 2025
 */

"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import Header from "@/components/Header";
import WelcomePage from "@/components/WelcomePage";
import InputBar from "@/components/InputBar";
import ChatMessageComponent from "@/components/ChatMessage";
import SessionSidebar from "@/components/SessionSidebar";
import {
  DemoScenario,
  DEMO_SCENARIOS,
  ChatSession,
  ChatMessage,
  AgentStep,
  UploadSummary,
  EventDigest,
  EventDigestItem,
} from "@/lib/types";
import { parseSSEBuffer } from "@/lib/sseParse";
import { getBundleStageLabel } from "@/lib/bundleStatus";

const MAX_STATUS_POLL_SECONDS = 600;
const MAX_STATUS_ERROR_RETRIES = 5;
const DRAFT_SESSION_ID = "__draft__";
const ACTIVE_SESSION_STORAGE_KEY = "fota_active_session_id";

/** 将后端 event 对象转为统一结构：优先用 aligned_timestamp，否则 raw_timestamp */
function _toDigestItem(ev: Record<string, unknown>): EventDigestItem {
  const aligned = typeof ev.aligned_timestamp === "number" ? ev.aligned_timestamp : undefined;
  const raw = typeof ev.raw_timestamp === "number" ? ev.raw_timestamp : undefined;
  const MIN_VALID_TS = 1577836800;
  const ts = aligned && aligned >= MIN_VALID_TS ? aligned : raw;
  return {
    eventType: String(ev.event_type ?? ""),
    timestamp: ts,
    controller: String(ev.controller ?? ""),
    rawLine: typeof ev.raw_line === "string" ? ev.raw_line.slice(0, 200) : undefined,
  };
}

function computeEventDigest(events: unknown[]): EventDigest {
  const CRITICAL_TYPES = new Set(["panic_or_fatal", "kernel_oops_or_bug", "kernel_watchdog"]);
  const FOTA_RESULT_TYPES = new Set(["fota_install_success", "fota_install_failure"]);

  const records = events.filter((e): e is Record<string, unknown> => typeof e === "object" && e !== null);

  let lastReboot: EventDigestItem | undefined;
  let lastCriticalFault: EventDigestItem | undefined;
  let fotaResult: (EventDigestItem & { success: boolean }) | undefined;
  let criticalCount = 0;

  for (const ev of records) {
    const type = String(ev.event_type ?? "");
    const item = _toDigestItem(ev);
    const ts = item.timestamp ?? 0;

    if (type === "system_reboot") {
      if (!lastReboot || ts > (lastReboot.timestamp ?? 0)) lastReboot = item;
    }
    if (CRITICAL_TYPES.has(type)) {
      criticalCount++;
      if (!lastCriticalFault || ts > (lastCriticalFault.timestamp ?? 0)) lastCriticalFault = item;
    }
    if (FOTA_RESULT_TYPES.has(type)) {
      if (!fotaResult || ts > (fotaResult.timestamp ?? 0)) {
        fotaResult = { ...item, success: type === "fota_install_success" };
      }
    }
  }

  return { totalEvents: records.length, lastReboot, lastCriticalFault, fotaResult, criticalCount };
}

const createSessionId = (): string =>
  `session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const deriveSessionTitle = (messages: ChatMessage[]): string => {
  const firstUserMsg = messages.find((m) => m.role === "user" && m.content.trim().length > 0);
  if (!firstUserMsg) return "新会话";
  const normalized = firstUserMsg.content.replace(/\s+/g, " ").trim();
  return normalized.length > 24 ? `${normalized.slice(0, 24)}...` : normalized;
};

const createEmptySession = (id?: string): ChatSession => {
  const now = new Date();
  return {
    id: id ?? createSessionId(),
    title: "新会话",
    messages: [],
    createdAt: now,
    updatedAt: now,
    titleSource: "default",
    titleAutoOptimized: false,
    turnCount: 0,
  };
};

type SessionTitleApiResponse = {
  title?: string;
};

type PersistedChatMessage = Omit<ChatMessage, "timestamp"> & {
  timestamp: string;
};

type PersistedChatSession = Omit<ChatSession, "messages" | "createdAt" | "updatedAt"> & {
  messages: PersistedChatMessage[];
  createdAt: string;
  updatedAt: string;
};

const parseDateOrNow = (value: unknown): Date => {
  if (typeof value === "string" || value instanceof Date) {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  return new Date();
};

const deserializeSession = (raw: unknown): ChatSession | null => {
  if (!raw || typeof raw !== "object") return null;
  const candidate = raw as Partial<PersistedChatSession>;
  if (!candidate.id || typeof candidate.id !== "string") return null;
  const messages = Array.isArray(candidate.messages)
    ? candidate.messages.map((msg): ChatMessage => ({
        ...msg,
        timestamp: parseDateOrNow(msg.timestamp),
      }))
    : [];

  const titleSource = candidate.titleSource;
  const normalizedTitleSource: ChatSession["titleSource"] =
    titleSource === "auto"
    || titleSource === "auto_optimized"
    || titleSource === "manual"
      ? titleSource
      : "default";

  return {
    id: candidate.id,
    title: typeof candidate.title === "string" && candidate.title.trim()
      ? candidate.title
      : "新会话",
    messages,
    createdAt: parseDateOrNow(candidate.createdAt),
    updatedAt: parseDateOrNow(candidate.updatedAt),
    titleSource: normalizedTitleSource,
    titleAutoOptimized: Boolean(candidate.titleAutoOptimized),
    turnCount: typeof candidate.turnCount === "number" ? candidate.turnCount : 0,
  };
};

const serializeSession = (session: ChatSession): PersistedChatSession => ({
  id: session.id,
  title: session.title,
  messages: session.messages.map((msg) => ({
    ...msg,
    timestamp: msg.timestamp.toISOString(),
  })),
  createdAt: session.createdAt.toISOString(),
  updatedAt: session.updatedAt.toISOString(),
  titleSource: session.titleSource,
  titleAutoOptimized: session.titleAutoOptimized,
  turnCount: session.turnCount,
});


/**
 * SSE 事件载荷类型定义
 *
 * 定义了后端通过 SSE 推送的各种事件类型
 */
type SsePayload = {
  type: string;
  step?: AgentStep;
  stepNumber?: number;
  partialResult?: string;
  content?: string;
  sources?: ChatMessage["sources"];
  confidenceLevel?: ChatMessage["confidenceLevel"];
  // workspace_update fields
  file?: "notes.md" | "todo.md" | "focus.md";
  agent?: string;
  change?: string;
};

/**
 * 主页面组件
 *
 * 管理整个诊断对话的状态和交互逻辑
 */
export default function Home() {
  const [currentScenario, setCurrentScenario] = useState<DemoScenario>(
    DEMO_SCENARIOS[0]
  );
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [draftSession, setDraftSession] = useState<ChatSession>(() =>
    createEmptySession(DRAFT_SESSION_ID)
  );
  const [activeSessionId, setActiveSessionId] = useState<string>(DRAFT_SESSION_ID);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleInput, setTitleInput] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const chatScrollContainerRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const abortControllerRef = useRef<AbortController | null>(null);
  const sessionsRef = useRef<ChatSession[]>(sessions);
  const draftRef = useRef<ChatSession>(draftSession);
  const saveTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const resumedPollingKeysRef = useRef<Set<string>>(new Set());
  const [isHydrated, setIsHydrated] = useState(false);
  const activeSession = useMemo(
    () => (
      activeSessionId === DRAFT_SESSION_ID
        ? draftSession
        : sessions.find((s) => s.id === activeSessionId) ?? draftSession
    ),
    [activeSessionId, draftSession, sessions]
  );
  const activeMessages = useMemo(
    () => activeSession?.messages ?? [],
    [activeSession]
  );

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    if (!shouldAutoScrollRef.current) return;
    messagesEndRef.current?.scrollIntoView({ behavior });
  }, []);

  const updateConversation = useCallback(
    (
      sessionId: string,
      updater: (current: ChatSession) => ChatSession
    ) => {
      if (sessionId === DRAFT_SESSION_ID) {
        setDraftSession((prev) => updater(prev));
        return;
      }
      setSessions((prev) => prev.map((session) => (session.id === sessionId ? updater(session) : session)));
    },
    []
  );

  const updateSessionMessages = useCallback(
    (sessionId: string, updater: (current: ChatMessage[]) => ChatMessage[]) => {
      updateConversation(sessionId, (session) => {
        const nextMessages = updater(session.messages);
        return {
          ...session,
          messages: nextMessages,
          title:
            session.titleSource === "default" && nextMessages.length > 0
              ? deriveSessionTitle(nextMessages)
              : session.title,
          updatedAt: new Date(),
        };
      });
    },
    [updateConversation]
  );

  const getConversationSnapshot = useCallback(
    (sessionId: string): ChatSession | undefined => {
      if (sessionId === DRAFT_SESSION_ID) return draftRef.current;
      return sessionsRef.current.find((s) => s.id === sessionId);
    },
    []
  );

  const persistSessionSnapshot = useCallback(async (sessionId: string) => {
    if (sessionId === DRAFT_SESSION_ID) return;
    const snapshot = getConversationSnapshot(sessionId);
    if (!snapshot) return;
    try {
      await fetch(`/api/sessions/${sessionId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(serializeSession(snapshot)),
      });
    } catch {
      // 持久化失败不打断主流程，下一次状态变化会继续尝试
    }
  }, [getConversationSnapshot]);

  const schedulePersistSession = useCallback((sessionId: string, delayMs = 1200) => {
    if (!isHydrated || sessionId === DRAFT_SESSION_ID) return;
    const existing = saveTimersRef.current[sessionId];
    if (existing) {
      return;
    }
    saveTimersRef.current[sessionId] = setTimeout(() => {
      void persistSessionSnapshot(sessionId);
      delete saveTimersRef.current[sessionId];
    }, delayMs);
  }, [isHydrated, persistSessionSnapshot]);

  const patchUploadFileProgress = useCallback((
    sessionId: string,
    uploadMessageId: string,
    fileName: string,
    patch: Partial<NonNullable<ChatMessage["uploadProgress"]>["files"][number]>,
    uploadPatch?: Partial<NonNullable<ChatMessage["uploadProgress"]>>
  ) => {
    updateSessionMessages(sessionId, (prev) =>
      prev.map((message) => {
        if (message.id !== uploadMessageId || !message.uploadProgress) return message;
        const nextFiles = message.uploadProgress.files.map((file) =>
          file.fileName === fileName ? { ...file, ...patch } : file
        );
        const percent = nextFiles.length > 0
          ? Math.round(nextFiles.reduce((sum, file) => sum + file.percent, 0) / nextFiles.length)
          : message.uploadProgress.percent;
        const allTerminal = nextFiles.every((file) => file.status === "completed" || file.status === "failed");
        const hasFailed = nextFiles.some((file) => file.status === "failed");
        return {
          ...message,
          uploadProgress: {
            ...message.uploadProgress,
            ...(uploadPatch ?? {}),
            files: nextFiles,
            percent,
            active: allTerminal ? false : (uploadPatch?.active ?? message.uploadProgress.active),
            stage: allTerminal
              ? (hasFailed ? "处理结束（含失败）" : "处理完成")
              : (uploadPatch?.stage ?? message.uploadProgress.stage),
            message: allTerminal
              ? (hasFailed ? "上传和解析结束，部分文件失败" : "上传和解析流程已结束")
              : (uploadPatch?.message ?? message.uploadProgress.message),
          },
        };
      })
    );
  }, [updateSessionMessages]);

  const ensureActiveSession = useCallback((): string => {
    if (activeSessionId !== DRAFT_SESSION_ID) return activeSessionId;
    const nextSession: ChatSession = {
      ...draftRef.current,
      id: createSessionId(),
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    setSessions((prev) => [nextSession, ...prev]);
    setActiveSessionId(nextSession.id);
    setDraftSession(createEmptySession(DRAFT_SESSION_ID));
    return nextSession.id;
  }, [activeSessionId]);

  useEffect(() => {
    scrollToBottom();
  }, [activeMessages, scrollToBottom]);

  useEffect(() => {
    const container = chatScrollContainerRef.current;
    if (!container) return;

    const updateAutoScrollFlag = () => {
      const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
      shouldAutoScrollRef.current = distanceToBottom <= 80;
    };

    updateAutoScrollFlag();
    container.addEventListener("scroll", updateAutoScrollFlag, { passive: true });
    return () => {
      container.removeEventListener("scroll", updateAutoScrollFlag);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const restoreSessions = async () => {
      try {
        const resp = await fetch("/api/sessions");
        if (!resp.ok) return;
        const payload = await resp.json();
        if (!Array.isArray(payload)) return;
        const restored = payload
          .map((item) => deserializeSession(item))
          .filter((item): item is ChatSession => Boolean(item))
          .sort((a, b) => b.updatedAt.getTime() - a.updatedAt.getTime());
        if (cancelled) return;

        setSessions(restored);
        if (restored.length === 0) return;

        let preferredId: string | null = null;
        if (typeof window !== "undefined") {
          preferredId = window.localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY);
        }
        const existing = preferredId
          ? restored.find((item) => item.id === preferredId)
          : null;
        const selectedSession = existing ?? restored[0];
        setActiveSessionId(selectedSession.id);
      } catch {
        // Ignore hydration errors, page still works as in-memory mode
      } finally {
        if (!cancelled) {
          setIsHydrated(true);
        }
      }
    };

    void restoreSessions();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);

  useEffect(() => {
    draftRef.current = draftSession;
  }, [draftSession]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!isHydrated) return;
    if (activeSessionId === DRAFT_SESSION_ID) {
      window.localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
      return;
    }
    window.localStorage.setItem(ACTIVE_SESSION_STORAGE_KEY, activeSessionId);
  }, [activeSessionId, isHydrated]);

  useEffect(() => {
    sessions.forEach((session) => schedulePersistSession(session.id));
  }, [sessions, schedulePersistSession]);

  useEffect(() => {
    if (!isHydrated) return;

    sessions.forEach((session) => {
      session.messages.forEach((message) => {
        if (!message.uploadProgress?.active) return;
        message.uploadProgress.files.forEach((file) => {
          if (!file.bundleId) return;
          if (file.status === "completed" || file.status === "failed") return;
          const resumeKey = `${session.id}:${message.id}:${file.fileName}:${file.bundleId}`;
          if (resumedPollingKeysRef.current.has(resumeKey)) return;
          resumedPollingKeysRef.current.add(resumeKey);

          void (async () => {
            let terminal = false;
            for (let i = 0; i < MAX_STATUS_POLL_SECONDS; i += 1) {
              if (i > 0) {
                await new Promise((r) => setTimeout(r, 1000));
              }
              const resp = await fetch(`/api/bundle-status/${encodeURIComponent(file.bundleId!)}`);
              let payload: Record<string, unknown> = {};
              try {
                payload = (await resp.json()) as Record<string, unknown>;
              } catch {
                payload = {};
              }
              if (!resp.ok) {
                continue;
              }
              const status = String(payload?.status ?? "running");
              const progress = typeof payload?.progress === "number" ? payload.progress : 0;
              const stageLabel = getBundleStageLabel(status);
              patchUploadFileProgress(
                session.id,
                message.id,
                file.fileName,
                {
                  status: status === "done" ? "completed" : status === "failed" ? "failed" : "processing",
                  percent: Math.max(8, Math.round(progress * 100)),
                  stage: stageLabel,
                  message: stageLabel,
                  bundleId: file.bundleId,
                  ...(status === "failed"
                    ? { error: String(payload?.error || "未知错误") }
                    : {}),
                },
                {
                  stage: `${file.fileName} - ${stageLabel}`,
                  message: `${file.fileName} - ${stageLabel}`,
                }
              );
              if (status === "done" || status === "failed") {
                terminal = true;
                break;
              }
            }
            if (!terminal) {
              resumedPollingKeysRef.current.delete(resumeKey);
            }
          })();
        });
      });
    });
  }, [isHydrated, patchUploadFileProgress, sessions]);

  useEffect(() => () => {
    Object.values(saveTimersRef.current).forEach((timer) => clearTimeout(timer));
  }, []);

  const handleScenarioChange = (scenario: DemoScenario) => {
    setCurrentScenario(scenario);
  };

  const handleSelectSession = (sessionId: string) => {
    setActiveSessionId(sessionId);
    setIsEditingTitle(false);
  };

  const handleDeleteSession = useCallback(async (sessionId: string) => {
    if (!sessionId || sessionId === DRAFT_SESSION_ID) return;

    const resp = await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
    if (!resp.ok) return;

    const pendingTimer = saveTimersRef.current[sessionId];
    if (pendingTimer) {
      clearTimeout(pendingTimer);
      delete saveTimersRef.current[sessionId];
    }

    const snapshot = sessionsRef.current;
    const remaining = snapshot.filter((session) => session.id !== sessionId);
    setSessions(remaining);

    if (activeSessionId === sessionId) {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      setIsRunning(false);
      setIsEditingTitle(false);
      setActiveSessionId(remaining[0]?.id ?? DRAFT_SESSION_ID);
    }
  }, [activeSessionId]);

  const handleCreateSession = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsRunning(false);
    setDraftSession(createEmptySession(DRAFT_SESSION_ID));
    setActiveSessionId(DRAFT_SESSION_ID);
    setIsEditingTitle(false);
  };

  const requestGeneratedTitle = useCallback(
    async (sessionId: string, mode: "initial" | "optimize"): Promise<string | null> => {
      const snapshot = getConversationSnapshot(sessionId);
      if (!snapshot) return null;
      const payload = {
        mode,
        messages: snapshot.messages
          .filter((m) => m.content.trim().length > 0)
          .slice(-8)
          .map((m) => ({ role: m.role, content: m.content })),
      };
      if (payload.messages.length === 0) return null;
      try {
        const resp = await fetch("/api/session-title", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!resp.ok) return null;
        const data = (await resp.json()) as SessionTitleApiResponse;
        return (data.title || "").trim() || null;
      } catch {
        return null;
      }
    },
    [getConversationSnapshot]
  );

  const applyTitle = useCallback(
    (sessionId: string, title: string, source: ChatSession["titleSource"]) => {
      updateConversation(sessionId, (session) => ({
        ...session,
        title: title.trim() || session.title,
        titleSource: source,
        updatedAt: new Date(),
      }));
    },
    [updateConversation]
  );

  const maybeAutoTitle = useCallback(
    async (sessionId: string, completedTurnCount: number) => {
      const snapshot = getConversationSnapshot(sessionId);
      if (!snapshot || sessionId === DRAFT_SESSION_ID) return;

      if (completedTurnCount === 1) {
        if (snapshot.titleSource === "manual") return;
        const title = await requestGeneratedTitle(sessionId, "initial");
        if (title) {
          applyTitle(sessionId, title, "auto");
        }
        return;
      }

      if (
        completedTurnCount <= 3
        && !snapshot.titleAutoOptimized
        && snapshot.titleSource !== "manual"
      ) {
        const title = await requestGeneratedTitle(sessionId, "optimize");
        if (title) {
          updateConversation(sessionId, (session) => ({
            ...session,
            title,
            titleSource: "auto_optimized",
            titleAutoOptimized: true,
            updatedAt: new Date(),
          }));
        }
      }
    },
    [applyTitle, getConversationSnapshot, requestGeneratedTitle, updateConversation]
  );

  const handleSaveTitle = () => {
    const normalized = titleInput.trim();
    if (!normalized) {
      setIsEditingTitle(false);
      setTitleInput(activeSession?.title ?? "新会话");
      return;
    }
    applyTitle(activeSessionId, normalized, "manual");
    setIsEditingTitle(false);
  };

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    setIsRunning(false);
    updateSessionMessages(activeSessionId, (prev) =>
      prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m))
    );
  };

  const handleUploadFiles = async (files: FileList | File[]) => {
    const fileArray = Array.from(files);
    if (fileArray.length === 0) return;
    shouldAutoScrollRef.current = true;
    const targetSessionId = ensureActiveSession();
    const uploadMessageId = `${Date.now()}-upload`;
    const uploadLines = fileArray.map((file) => `- ${file.name}`).join("\n");
    const initialUploadMessage: ChatMessage = {
      id: uploadMessageId,
      role: "user",
      content: `上传文件：\n${uploadLines}`,
      timestamp: new Date(),
      uploadProgress: {
        active: true,
        percent: 0,
        stage: "准备上传",
        message: "等待开始",
        files: fileArray.map((file) => ({
          fileName: file.name,
          status: "queued",
          percent: 0,
          stage: "排队中",
          message: "等待上传",
        })),
      },
    };
    updateSessionMessages(targetSessionId, (prev) => [...prev, initialUploadMessage]);

    const updateUploadMessage = (
      updater: (message: ChatMessage) => ChatMessage
    ) => {
      updateSessionMessages(targetSessionId, (prev) =>
        prev.map((message) => (message.id === uploadMessageId ? updater(message) : message))
      );
    };

    const withUpdatedFile = (
      message: ChatMessage,
      fileName: string,
      patch: Partial<NonNullable<ChatMessage["uploadProgress"]>["files"][number]>
    ) => {
      const progress = message.uploadProgress;
      if (!progress) return message;
      const filesState = progress.files.map((item) =>
        item.fileName === fileName ? { ...item, ...patch } : item
      );
      const aggregatePercent = filesState.length > 0
        ? Math.round(filesState.reduce((sum, item) => sum + item.percent, 0) / filesState.length)
        : progress.percent;
      return {
        ...message,
        uploadProgress: {
          ...progress,
          percent: aggregatePercent,
          files: filesState,
        },
      };
    };

    const uploadSummaries: UploadSummary[] = [];
    const finalFileStates = new Map<string, "completed" | "failed" | "processing">();
    for (const file of fileArray) {
      const form = new FormData();
      form.append("file", file);

      try {
        updateUploadMessage((message) => {
          const next = withUpdatedFile(message, file.name, {
            status: "uploading",
            percent: 3,
            stage: "上传中",
            message: "文件上传中",
          });
          if (!next.uploadProgress) return next;
          return {
            ...next,
            uploadProgress: {
              ...next.uploadProgress,
              stage: "上传中",
              message: `正在上传 ${file.name}`,
            },
          };
        });
        const resp = await fetch("/api/upload-log", {
          method: "POST",
          body: form,
        });
        const payload = await resp.json();
        if (!resp.ok) {
          finalFileStates.set(file.name, "failed");
          updateUploadMessage((message) => withUpdatedFile(message, file.name, {
            status: "failed",
            percent: 100,
            stage: "上传失败",
            message: "上传失败",
            error: String(payload?.detail || payload?.error?.message || payload?.error || resp.status),
          }));
          continue;
        }
        const bundleId = payload?.bundle_id;
        if (!bundleId) {
          finalFileStates.set(file.name, "failed");
          updateUploadMessage((message) => withUpdatedFile(message, file.name, {
            status: "failed",
            percent: 100,
            stage: "提交失败",
            message: "未返回 bundle_id",
            error: "未返回 bundle_id",
          }));
          continue;
        }
        updateUploadMessage((message) => withUpdatedFile(message, file.name, {
          bundleId,
          status: "processing",
          percent: 8,
          stage: "已提交，等待处理",
          message: "已入队，等待处理",
        }));

        let finalStatus: {
          status?: string;
          progress?: number;
          error?: string | null;
          file_count?: number;
          files_by_controller?: Record<string, number>;
          valid_time_range_by_controller?: Record<string, { start?: number; end?: number }>;
        } | null = null;
        let lastObservedStatus = "queued";
        let lastObservedProgress = 0;
        let statusQueryError: string | null = null;
        let consecutiveStatusErrors = 0;
        for (let i = 0; i < MAX_STATUS_POLL_SECONDS; i += 1) {
          await new Promise((r) => setTimeout(r, 1000));
          const stResp = await fetch(`/api/bundle-status/${bundleId}`);
          let stPayload: Record<string, unknown> = {};
          try {
            stPayload = (await stResp.json()) as Record<string, unknown>;
          } catch {
            stPayload = {};
          }
          if (!stResp.ok) {
            consecutiveStatusErrors += 1;
            const errText = String(
              stPayload?.detail
              || (typeof stPayload?.error === "object" && stPayload?.error
                ? (stPayload.error as { message?: string }).message
                : stPayload?.error)
              || stResp.status
            );
            updateUploadMessage((message) => withUpdatedFile(message, file.name, {
              status: "processing",
              stage: "状态查询异常",
              message: `重试中 (${consecutiveStatusErrors}/${MAX_STATUS_ERROR_RETRIES})`,
              bundleId,
            }));
            if (consecutiveStatusErrors >= MAX_STATUS_ERROR_RETRIES) {
              statusQueryError = errText;
              break;
            }
            continue;
          }
          consecutiveStatusErrors = 0;
          const progress = typeof stPayload?.progress === "number" ? stPayload.progress : 0;
          const status = String(stPayload?.status ?? "running");
          lastObservedStatus = status;
          lastObservedProgress = progress;
          const stageLabel = getBundleStageLabel(status);
          updateUploadMessage((message) => {
            const next = withUpdatedFile(message, file.name, {
              status: "processing",
              percent: Math.max(8, Math.round(progress * 100)),
              stage: stageLabel,
              message: `${stageLabel}`,
              bundleId,
            });
            if (!next.uploadProgress) return next;
            return {
              ...next,
              uploadProgress: {
                ...next.uploadProgress,
                stage: stageLabel,
                message: `${file.name} - ${stageLabel}`,
              },
            };
          });
          if (stPayload?.status === "done" || stPayload?.status === "failed") {
            finalStatus = stPayload;
            break;
          }
        }

        if (statusQueryError) {
          finalFileStates.set(file.name, "failed");
          updateUploadMessage((message) => withUpdatedFile(message, file.name, {
            status: "failed",
            percent: 100,
            stage: "状态查询失败",
            message: statusQueryError,
            error: statusQueryError,
            bundleId,
          }));
          continue;
        }

        if (!finalStatus) {
          const stageLabel = getBundleStageLabel(lastObservedStatus);
          finalFileStates.set(file.name, "processing");
          updateUploadMessage((message) => withUpdatedFile(message, file.name, {
            status: "processing",
            percent: Math.max(8, Math.round(lastObservedProgress * 100)),
            stage: stageLabel,
            message: "后台处理中，可在消息内继续查看状态",
            bundleId,
          }));
          continue;
        }

        if (finalStatus.status === "failed") {
          finalFileStates.set(file.name, "failed");
          updateUploadMessage((message) => withUpdatedFile(message, file.name, {
            status: "failed",
            percent: 100,
            stage: "处理失败",
            message: String(finalStatus?.error || "未知错误"),
            bundleId,
            error: String(finalStatus?.error || "未知错误"),
          }));
          continue;
        }

        finalFileStates.set(file.name, "completed");

        // 拉取事件摘要（上传完成后立即获取，不阻塞 UI）
        let eventDigest: EventDigest | undefined;
        try {
          const evResp = await fetch(`/api/bundle-events/${encodeURIComponent(bundleId)}?limit=500`);
          if (evResp.ok) {
            const evData: unknown = await evResp.json();
            if (Array.isArray(evData)) eventDigest = computeEventDigest(evData);
          }
        } catch {
          // 摘要加载失败不影响主流程
        }

        uploadSummaries.push({
          bundleId,
          fileName: file.name,
          fileCount: finalStatus.file_count ?? 0,
          filesByController: finalStatus.files_by_controller ?? {},
          validTimeRangeByController: finalStatus.valid_time_range_by_controller ?? {},
          eventDigest,
        });
        updateUploadMessage((message) => withUpdatedFile(message, file.name, {
          status: "completed",
          percent: 100,
          stage: "处理完成",
          message: "上传与解析完成",
          bundleId,
        }));
      } catch (err) {
        finalFileStates.set(file.name, "failed");
        updateUploadMessage((message) => withUpdatedFile(message, file.name, {
          status: "failed",
          percent: 100,
          stage: "上传异常",
          message: (err as Error).message,
          error: (err as Error).message,
        }));
      }
    }
    updateUploadMessage((message) => {
      if (!message.uploadProgress) return message;
      const states = fileArray.map((file) => finalFileStates.get(file.name) ?? "failed");
      const hasProcessing = states.some((state) => state === "processing");
      const hasFailed = states.some((state) => state === "failed");
      const aggregatedPercent = message.uploadProgress.files.length > 0
        ? Math.round(message.uploadProgress.files.reduce((sum, file) => sum + file.percent, 0) / message.uploadProgress.files.length)
        : message.uploadProgress.percent;
      return {
        ...message,
        uploadProgress: {
          ...message.uploadProgress,
          active: hasProcessing,
          percent: hasProcessing ? Math.max(8, Math.min(99, aggregatedPercent)) : 100,
          stage: hasProcessing ? "后台处理中" : (hasFailed ? "处理结束（含失败）" : "处理完成"),
          message: hasProcessing
            ? "部分文件仍在后台处理中，可在消息内继续查看状态"
            : (hasFailed ? "上传和解析结束，部分文件失败" : "上传和解析流程已结束"),
        },
      };
    });
    if (uploadSummaries.length > 0) {
      const summaryMessage: ChatMessage = {
        id: `${Date.now()}-upload-summary`,
        role: "system",
        systemKind: "upload_summary",
        content: "日志上传汇总",
        timestamp: new Date(),
        uploadSummaries,
      };
      updateSessionMessages(targetSessionId, (prev) => [...prev, summaryMessage]);
    }
  };

  const handleSend = async (message: string, scenarioId?: string) => {
    if (isRunning) return;
    // 若预设问题指定了场景，自动切换
    const effectiveScenarioId = scenarioId ?? currentScenario.id;
    if (scenarioId && scenarioId !== currentScenario.id) {
      const target = DEMO_SCENARIOS.find((s) => s.id === scenarioId);
      if (target) setCurrentScenario(target);
    }
    shouldAutoScrollRef.current = true;
    const targetSessionId = ensureActiveSession();
    const currentSnapshot = getConversationSnapshot(targetSessionId);

    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      role: "user",
      content: message,
      timestamp: new Date(),
    };

    const assistantId = (Date.now() + 1).toString();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      thinking: { steps: [], isExpanded: true },
      timestamp: new Date(),
      isStreaming: true,
    };

    updateSessionMessages(targetSessionId, (prev) => [...prev, userMessage, assistantMessage]);
    setIsRunning(true);

    const historyPayload = (currentSnapshot?.messages ?? []).map((m) => ({
      role: m.role,
      content: m.content,
    }));

    // 提取当前会话中最近一条已完成的上传日志包 ID，传给后端 Agent 分析真实日志
    const activeBundleId = (currentSnapshot?.messages ?? [])
      .slice()
      .reverse()
      .flatMap((m) => m.uploadSummaries ?? [])
      .find((s) => s.bundleId)?.bundleId ?? null;

    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    const applySsePayload = (data: SsePayload) => {
      switch (data.type) {
        case "step_start":
          updateSessionMessages(targetSessionId, (prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    thinking: {
                      ...m.thinking!,
                      steps: [...m.thinking!.steps, data.step as AgentStep],
                    },
                  }
                : m
            )
          );
          break;

        case "step_progress":
          updateSessionMessages(targetSessionId, (prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    thinking: {
                      ...m.thinking!,
                      steps: m.thinking!.steps.map((s) =>
                        s.stepNumber === data.stepNumber
                          ? { ...s, result: data.partialResult }
                          : s
                      ),
                    },
                  }
                : m
            )
          );
          break;

        case "step_complete":
          updateSessionMessages(targetSessionId, (prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    thinking: {
                      ...m.thinking!,
                      steps: m.thinking!.steps.map((s) =>
                        s.stepNumber === data.step?.stepNumber
                          ? (data.step as AgentStep)
                          : s
                      ),
                    },
                  }
                : m
            )
          );
          break;

        case "content_delta":
          updateSessionMessages(targetSessionId, (prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m;
              const chunk = data.content ?? "";
              const next =
                m.content === ""
                  ? chunk.replace(/^\n+/, "")
                  : m.content + chunk;
              return { ...m, content: next };
            })
          );
          break;

        case "content_complete":
          updateSessionMessages(targetSessionId, (prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    ...(data.content ? { content: data.content } : {}),
                    sources: data.sources,
                    confidenceLevel: data.confidenceLevel,
                    isStreaming: false,
                    thinking: {
                      ...m.thinking!,
                      isExpanded: false,
                    },
                  }
                : m
            )
          );
          break;

        case "workspace_update": {
          // Accumulate workspace updates onto the matching agent step
          const wsUpdate = {
            file: data.file ?? "notes.md",
            agent: data.agent ?? "",
            change: data.change ?? "",
            timestamp: new Date().toISOString(),
          } as import("@/lib/types").WorkspaceUpdate;

          updateSessionMessages(targetSessionId, (prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m;
              const updatedSteps = m.thinking!.steps.map((s) => {
                if (s.agentName !== data.agent) return s;
                return {
                  ...s,
                  workspaceUpdates: [...(s.workspaceUpdates ?? []), wsUpdate],
                };
              });
              return { ...m, thinking: { ...m.thinking!, steps: updatedSteps } };
            })
          );
          break;
        }

        case "done":
          setIsRunning(false);
          {
            const snapshot = getConversationSnapshot(targetSessionId);
            const completedTurnCount = (snapshot?.turnCount ?? 0) + 1;
            updateConversation(targetSessionId, (session) => ({
              ...session,
              turnCount: completedTurnCount,
              updatedAt: new Date(),
            }));
            void maybeAutoTitle(targetSessionId, completedTurnCount);
          }
          break;
      }
    };

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          scenarioId: effectiveScenarioId,
          history: historyPayload,
          ...(activeBundleId ? { bundleId: activeBundleId } : {}),
        }),
        signal: abortController.signal,
      });

      if (!response.body) return;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (value) {
          buffer += decoder.decode(value, { stream: true });
        }
        if (done) {
          buffer += decoder.decode();
          const { events } = parseSSEBuffer(buffer);
          for (const evt of events) {
            applySsePayload(evt as SsePayload);
          }
          break;
        }
        const { events, rest } = parseSSEBuffer(buffer);
        buffer = rest;
        for (const evt of events) {
          applySsePayload(evt as SsePayload);
        }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      console.error("Stream error:", err);
      setIsRunning(false);
      updateSessionMessages(targetSessionId, (prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                content: "抱歉，处理请求时出现错误。请重试。",
                isStreaming: false,
              }
            : m
        )
      );
    }
  };

  const hasMessages = activeMessages.length > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: "var(--bg-primary)" }}>
      <Header
        currentScenario={currentScenario}
        onScenarioChange={handleScenarioChange}
      />

      <main style={{ flex: 1, overflow: "hidden", display: "flex" }}>
        <SessionSidebar
          sessions={sessions}
          activeSessionId={activeSessionId === DRAFT_SESSION_ID ? undefined : activeSessionId}
          onSelectSession={handleSelectSession}
          onCreateSession={handleCreateSession}
          onDeleteSession={handleDeleteSession}
        />

        <div
          style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}
        >
          <div
            className="border-b px-4 py-3"
            style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}
          >
            {isEditingTitle ? (
              <div className="flex items-center gap-2">
                <input
                  value={titleInput}
                  onChange={(e) => setTitleInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleSaveTitle();
                    if (e.key === "Escape") {
                      setIsEditingTitle(false);
                      setTitleInput(activeSession?.title ?? "新会话");
                    }
                  }}
                  className="w-full rounded-md border px-3 py-1.5 text-sm outline-none"
                  style={{
                    borderColor: "var(--border-color)",
                    background: "var(--bg-secondary)",
                    color: "var(--text-primary)",
                  }}
                  autoFocus
                />
                <button
                  type="button"
                  onClick={handleSaveTitle}
                  className="rounded-md px-3 py-1.5 text-sm"
                  style={{ background: "var(--accent-blue)", color: "#fff" }}
                >
                  保存
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setTitleInput(activeSession?.title ?? "新会话");
                  setIsEditingTitle(true);
                }}
                className="text-left text-sm font-medium"
                style={{ color: "var(--text-primary)" }}
                title="点击编辑标题"
              >
                {activeSession?.title || "新会话"}
              </button>
            )}
          </div>

          <div
            ref={chatScrollContainerRef}
            style={{ flex: 1, overflowY: "auto" }}
          >
            {!hasMessages ? (
              <WelcomePage onQuestionClick={handleSend} />
            ) : (
              <div style={{ maxWidth: "48rem", margin: "0 auto", padding: "24px 16px", width: "100%" }}>
                {activeMessages.map((msg) => (
                  <ChatMessageComponent key={msg.id} message={msg} />
                ))}
                <div ref={messagesEndRef} />
              </div>
            )}
          </div>

          <InputBar
            onSend={handleSend}
            isRunning={isRunning}
            onStop={handleStop}
            onUploadFiles={handleUploadFiles}
          />
        </div>
      </main>
    </div>
  );
}
