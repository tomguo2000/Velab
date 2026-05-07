# Changelog

本文件记录 Velab FOTA 智能诊断平台的所有重要变更，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [Unreleased]

### Fixed
- **Scenario B 模型全部落到 Haiku**：`llm.py` 的 `_resolve_anthropic_model()` / `_resolve_openai_model()` 原将三个虚拟别名（`router-model`、`agent-model`、`synthesizer-model`）统一映射到同一个 `ANTHROPIC_DEFAULT_MODEL`（默认 `claude-haiku-4-5-20251001`），导致场景 B 直连模式下所有 Agent（含 RCA 根因分析）均跑 Haiku。
- **`rca_synthesizer.py` 错用 `agent-model`**：`_llm_synthesize()` 中 `model="agent-model"` 改为 `model="synthesizer-model"`，确保 RCA 走 Synthesizer 层路由。

### Changed
- `services/llm.py`：三层别名分别映射，各自支持独立环境变量覆盖：
  - `router-model` → `ANTHROPIC_ROUTER_MODEL`（默认 `claude-haiku-4-5-20251001`）/ `OPENAI_ROUTER_MODEL`（默认 `gpt-4o-mini`）
  - `agent-model` → `ANTHROPIC_AGENT_MODEL`（默认 `claude-sonnet-4-6`）/ `OPENAI_AGENT_MODEL`（默认 `gpt-4o`）
  - `synthesizer-model` → `ANTHROPIC_SYNTHESIZER_MODEL`（默认 `claude-sonnet-4-6`）/ `OPENAI_SYNTHESIZER_MODEL`（默认 `gpt-4o`）
- `backend/.env` / `.env.example`：废弃 `ANTHROPIC_DEFAULT_MODEL`，替换为三条分层配置注释。
- `_pick_available_anthropic_model()` 降级列表：首位改为读取 `ANTHROPIC_AGENT_MODEL`，`claude-sonnet-4-6` 置于优先队首。

---

## [2026-05-07]

### Added
- `LogAnalyticsAgent` 新增可选 `time_hint` 参数支持：用户用自然语言描述故障时间（如"9月11日凌晨"、"晚上21点"），LLM 提取后通过 orchestrator 注入 agent context，将日志查询窗口缩窄至相关时段；无法解析时自动退化为全量分析。
- 新增 `_parse_time_hint()` 函数，支持中文月日、时段限定词（凌晨/上午/下午/晚上等）及精确小时的解析，以 bundle 有效时间范围为日历参考锚点。
- `LogAnalyticsAgent.tool_schema()` 覆盖方法：在 LLM 函数调用 schema 中暴露可选 `time_hint` 字段。
- `orchestrator._run_agent()` 注入 `time_hint` 到 `agent_context`。

---

## [2026-05-03 23:59]

### Added
- **EventDigest 上传摘要卡**：日志包处理完成后，聊天界面自动展示事件摘要（最近重启、最后严重故障、FOTA 升级结果及事件计数）。
  - `web/src/lib/types.ts`：新增 `EventDigestItem`、`EventDigest` 接口；`UploadSummary` 扩展可选 `eventDigest` 字段。
  - `web/src/app/page.tsx`：`handleSend()` 在 bundle 处理完成后调用 `/api/bundle-events/{id}?limit=500`，计算 digest 并附加到 `uploadSummaries`。
  - `web/src/components/UploadSummaryCard.tsx`：新增 `EventDigestPanel` 组件，渲染重启/故障/FOTA 结果行。
- **RAR 归档上传支持**：`log_pipeline/ingest/extractor.py` 支持 `.rar` 格式，依赖系统 `unrar`；`requirements.txt` 新增 `rarfile==4.2`。
- **裸文件上传支持**：`.log / .txt / .dlt` 文件可直接上传，不再要求打包成压缩档。
- **一键开发脚本 `scripts/dev.sh`**：启动前自动清理旧进程（SIGTERM→SIGKILL），按 `DEPLOYMENT_MODE` 决定是否启动 LiteLLM Gateway。
- **本地 CI 验证脚本 `scripts/test-ci.sh`**：复刻完整 CI 流程（PostgreSQL/Redis 前置检查 → flake8 → pytest → eslint → vitest），发 PR 前必跑。
- **Copilot 辅助开发体系**：`.agents/skills/` 25 个领域技能文件 + `.github/agents/` 3 个 Persona 文件 + `.github/copilot-instructions.md`。
- **后端集成测试补全**（共新增 8 个文件 131 个用例）：覆盖 `session_title`、`rca_synthesizer`、`doc_chunker`、`feedback_api`、`jira_knowledge`、`doc_retrieval`、`evaluation`、`log_analytics_bundle`，及 `log_pipeline/tests/test_http_upload.py`（10 用例）。
- **后端单元测试补全**（新增 4 个文件 63 个用例）：覆盖 `common/redaction.py`（VIN/手机号/车牌脱敏 + async 装饰器）、`common/chain_log.py`（trace_id 管理 + step_timer）、`services/tool_functions.py`、`services/semantic_cache.py`。
- **前端 API Route 测试**（新增 7 个文件 16 个用例）：sessions、sessions/[sessionId]、session-title、upload-log、bundle-status/[bundleId]、bundle-events/[bundleId]、bundle-logs/[bundleId] 全覆盖。

### Fixed
- **Bundle-Agent 断链**：`LogAnalyticsAgent._load_logs()` 原固定读取 `data/logs/` mock 数据，与上传的真实日志完全断链。新增 `_load_logs_from_bundle()` 方法，正确读取 `valid_time_range_by_controller` 字段（修复字段名错误 `valid_time_range` → `valid_time_range_by_controller`）。
- **catalog.py 时间范围计算**：SQL CASE 表达式的 `ELSE NULL` 改为 `ELSE valid_ts_min/max`，修复 FOTA Java 文本日志（`clock_offset=NULL`、时间戳已是 wall-clock）被排除在有效时间范围之外的问题。
- **`_file_overlaps()` 空窗口**：`range_query.py` 修复 `clock_offset is None` 时错误返回 `False`，导致有效日志文件被跳过。
- **`unsynced_files` 双重发送**：修复已在窗口集中的文件又出现在 `unsynced_files` 列表的问题，条件增加 `valid_ts_min is None`。
- **`_extract_plain` UUID 前缀 Bug**：上传文件保存为 `{uuid32}__原始名`，修复后使用原始文件名供分类器匹配（`_UPLOAD_PREFIX_RE` 剥离前缀）。
- **路径遍历漏洞**：`_should_skip()` 新增 `".." in parts` 和绝对路径检测，阻断 RAR/ZIP/TAR 归档中的任意文件写入攻击（[C-2]）。
- **`main.py` bundle_id UUID 校验**：API 边界增加 `re.fullmatch` UUID 格式验证，非法值返回 400（[I-1]）。
- **orchestrator bundle_id 防伪造**：从 `conversation_history` 自动提取的 `bundleId` 增加正则校验（[I-2]）。
- **前端 XSS 防护**：`ChatMessage.tsx` 新增 `escapeHtml()`/`sanitizeUrl()`；`next.config.ts` 添加 5 个安全响应头（[I-5]）。
- **CI 修复**：`ci.yml` 补全 `POSTGRES_DB` 等环境变量；新增 Redis 7 service；flake8 增加 `--exclude=venv,.venv`。
- **前端 CVE 修复**：`npm audit fix` 修复 Vite 3 个 HIGH CVE；`package.json overrides` 强制 `postcss ≥ 8.5.10`。

### Changed
- `backend/main.py` CORS 由通配符收窄为 `ALLOWED_ORIGINS` 环境变量控制。
- `orchestrator.orchestrate()` 新增 `bundle_id` 参数，未传时自动从 `conversation_history` 中扫描最近 `upload_summary` 提取。
- `web/src/app/api/chat/route.ts` 新增 `bundleId` 校验，透传给后端 `/chat` 端点。
- `vitest.config.ts` 新增覆盖率红线：branches ≥ 70%，functions ≥ 70%，lines ≥ 80%，statements ≥ 80%。
- `vector_search.py` 原子写入：`.partial + os.replace()` 模式防止索引文件损坏（[I-4]）。

---

## [2026-04-10 03:36]

### Fixed
- **部署脚本加固**：
  - `backend/scripts/check_env.sh`：修复 `CRITICAL_PACKAGES` 格式（`pip包名:模块名`）、`set -e` + heredoc 失效问题、变量名与实际 env 不对齐三处 Bug。
  - `backend/scripts/deploy.sh`：Step 6 新增强随机 `POSTGRES_PASSWORD` 自动生成（检测弱密码 `fota_password` 时替换）；Step 8 systemd restart 后执行 `is-active` 验证。
  - `gateway/scripts/deploy.sh`：venv 检查改为检测 `venv/bin/pip` 存在性，防止不完整 venv 跳过重建。
  - `scripts/deploy-all.sh`：新增 `--mode`/`--domain` 命令行参数，支持非交互式 CI/CD 执行；对账阶段新增 LLM API Key 占位值检测和弱密码检测。

### Changed
- `config.py` 新增 `REDIS_PASSWORD` 字段；`LITELLM_API_KEY` 默认值对齐为 `sk-fota-virtual-key`。
- `gateway/systemd/litellm.service`：`--host`/`--port` 改为读取环境变量 `${HOST:-127.0.0.1}`/`${PORT:-4000}`。
- `backend/.env.example` / `gateway/.env.example`：补全注解、修正注释错误、新增 `GATEWAY_LOG_PATH` 文档化。

---

## [2026-04-06 18:57] — Sprint 4

### Added
- **DocRetrieval Agent**（第 3 个 Agent）：`agents/doc_retrieval.py`，加入 `SCENARIO_AGENT_MAP`。
- **向量检索服务**：`services/vector_search.py`，TF-IDF baseline，预留 embedding 接口。
- **语义缓存服务**：`services/semantic_cache.py`，SHA-256 精确匹配模式。
- **Agent Tool Use 函数**：`services/tool_functions.py`，实现 `extract_timeline_events`、`fetch_raw_line_context`、`search_fota_stage_transitions`。
- **PDF/文本切块服务**：`services/doc_chunker.py`，支持 3 种切块策略。
- **诊断反馈 API**：`api/feedback.py`，5 个端点。
- **Prometheus 监控指标**：`api/metrics.py`。
- **评测框架**：`services/evaluation.py`，5 个标准 case，5 维评分。
- 演示日志扩充至 5 份，Jira 工单扩充至 10 个。
- `vitest.config.ts` 添加覆盖率 thresholds。

### Changed
- `backend/main.py` 从废弃的 `@app.on_event` 迁移至 `lifespan` context manager。
- `agents/rca_synthesizer.py` 新增 `_validate_citations()` 引用 ID 断言验证。

---

## [2026-03-23 15:35]

### Added
- 多 Agent 协作架构初始实现：`LogAnalyticsAgent`、`JiraKnowledgeAgent`、`RCASynthesizerAgent`、`Orchestrator`。
- `BaseAgent` 抽象基类与 `registry` 全局注册表。
- FastAPI 后端骨架（SSE 流式响应、`/chat` 端点）。
- Next.js 前端骨架（App Router、SSE 消费、`ChatMessage` / `ThinkingProcess` 组件）。
- `common/chain_log.py`：全链路 trace_id 追踪。
- `common/redaction.py`：VIN 码、手机号、车牌号脱敏。
- `services/llm.py`：统一 LLM 客户端抽象，支持 LiteLLM Gateway 转发。
- `gateway/`：基于 LiteLLM 的模型网关配置（多模型路由、负载均衡）。
- `log_pipeline/` 子系统：DLT 解码器、FOTA 文本解码器、事件规则引擎、SQLite bundle catalog、NDJSON 流式查询。
- PostgreSQL + Redis 基础设施集成，systemd 服务单元文件。

