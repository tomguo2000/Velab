# Velab - FOTA 智能诊断平台

## 项目概览
基于 AI 的车辆固件升级 (FOTA) 诊断系统。采用多 Agent 协作架构，通过分析车辆日志、工单和技术文档，为技术人员提供智能化解决方案。

## 技术栈
- **后端 (Backend)**: Python 3.12, FastAPI (异步), Pydantic 2, OpenAI SDK, SSE-Starlette.
- **前端 (Frontend)**: Next.js 16 (App Router), React 19, Tailwind CSS 4, TypeScript 6.
- **测试**:
  - 后端 - Pytest
  - 前端 - Vitest 4.1.2, @vitest/coverage-v8, @vitest/ui
  - 测试库 - @testing-library/react 16.3.2, @testing-library/jest-dom 6.9.1
  - API Mock - MSW 2.12.14
  - Lint - ESLint 9

## 核心架构 & 文件夹说明
- `backend/`: FastAPI 后端核心。
  - `/agents/`: 所有的 Agent 逻辑。`base.py` 包含注册表。
    - `log_analytics.py` — 日志分析 Agent
    - `jira_knowledge.py` — Jira 工单检索 Agent
    - `doc_retrieval.py` — 技术文档检索 Agent (2026-04-06 新增)
    - `rca_synthesizer.py` — RCA 综合分析 Agent
    - `orchestrator.py` — 编排器
  - `/common/`: 全链路日志 (trace_id) 和脱敏 (redaction) 逻辑。
  - `/services/`: 核心服务层。
    - `llm.py` — 统一的 LLM 客户端抽象
    - `vector_search.py` — TF-IDF/向量检索服务 (2026-04-06 新增)
    - `semantic_cache.py` — 语义缓存服务 (2026-04-06 新增)
    - `tool_functions.py` — Agent Tool Use 函数 (2026-04-06 新增)
    - `doc_chunker.py` — PDF/文本切块服务 (2026-04-06 新增)
    - `evaluation.py` — 诊断评测框架 (2026-04-06 新增)
  - `/api/`: RESTful API 接口层（22 个端点）。
    - `feedback.py` — 诊断反馈 API (2026-04-06 新增)
    - `metrics.py` — Prometheus 监控指标 (2026-04-06 新增)
- `web/`: Next.js 前端应用。
  - `src/app/`: 页面和路由 (Page & API Routes).
  - `src/components/`: 高复用 React 组件（ChatMessage, ThinkingProcess）.
- `gateway/`: 基于 LiteLLM 的模型网关配置。

## 常用开发指令

### 后端
- **安装**: `pip install -r requirements.txt` (在 venv 中)
- **启动**: `python main.py` 或 `uvicorn main:app --reload`
- **测试**: `pytest`

### 前端
- **安装**: `npm install`
- **启动**: `npm run dev` (URL: http://localhost:3000)
- **测试**:
  - `npm test` - 运行所有测试
  - `npm run test:watch` - 监听模式
  - `npm run test:coverage` - 生成覆盖率报告
  - `npm run test:ui` - 可视化测试界面
- **检查**: `npm run lint`

### 发 PR 前（必须）
- **本地 CI 验证**: `bash scripts/test-ci.sh`
  复刻完整 CI 流程（PostgreSQL + Redis 前置检查 → flake8 → pytest → eslint → vitest）
  看到 `✅ 本地 CI 全部通过，可以提交 PR！` 后再推送
- **一键启动**: `bash scripts/dev.sh`（自动停旧进程 + 按 DEPLOYMENT_MODE 决定是否启动 Gateway）

## 编码与设计规范

### 1. 通用规则
- **Git 提交**: 遵循 Conventional Commits (例如: `feat:`, `fix:`, `docs:`)。
- **调用链追踪**: 所有后端方法应支持或生成 `trace_id` 用于全链路追踪。

### 2. 后端规范 (Python/FastAPI)
- **命名**: 使用 `snake_case`。所有异步方法名以 `async` 修饰。
- **Agent 注册**: 添加新 Agent 必须继承 `BaseAgent` 并在文件末尾手动调用 `registry.register()`。
- **敏感信息**: 严禁直接输出 VIN 码、手机号等。必须在 API 出口或日志记录处使用 `redactor` 装饰器或逻辑。

### 3. 前端规范 (TypeScript/React)
- **组件**: 使用函数式组件 (Functional Components)。
- **命名**: 组件名使用 `PascalCase`，变量和普通函数使用 `camelCase`。
- **样式**: 仅使用 Tailwind CSS 4 工具类，避免自定义纯 CSS。
- **类型**: 强制使用 TypeScript 类型，禁止无故使用 `any`。

## AI 记忆管理建议
- 每次完成重要架构调整或修复了复杂的逻辑 Bug，请在 `CLAUDE.md` 的末尾追加简短的“决策日志 (Decision Log)”，防止后续对话中的 AI 丢失上下文。
- 当我对你的开发流程提出修正时，请立即更新本文件的具体准则。

---

## Decision Log

### 2026-04-10: 部署脚本与 env 模板加固
- **config.py**: 新增 `REDIS_PASSWORD` 字段；`LITELLM_API_KEY` 默认值与 `.env.example` 对齐（`sk-fota-virtual-key`）
- **backend/.env.example**: 补全所有字段的 `config.py` 注解；清理 section 10 中已过时的 `DATABASE_URL`/`REDIS_URL` 注释行
- **gateway/.env.example**: 修正 section 1 注释（`synthesizer-model` 使用 `ANTHROPIC_API_KEY_1` 而非 `ANTHROPIC_API_KEY`）；新增 section 8 文档化 `GATEWAY_LOG_PATH`
- **gateway/systemd/litellm.service**: `--host`/`--port` 改为读取 `${HOST:-127.0.0.1}`/`${PORT:-4000}`，与 `.env` 联动
- **backend/scripts/check_env.sh**: 修复三处 Bug：① `CRITICAL_PACKAGES` 改为 `pip包名:模块名` 格式（`psycopg2-binary:psycopg2`、`python-dotenv:dotenv`）；② `set -e` + heredoc + `$?` 失效问题改为 `if python3 << EOF ... then/else` 模式；③ `REQUIRED_VARS`/`OPTIONAL_VARS` 变量名与实际 env 对齐
- **backend/scripts/deploy.sh**: Step 6 新增强随机 `POSTGRES_PASSWORD` 自动生成（检测弱密码 `fota_password` 时替换）；Step 7 加注 `create_all()` 不执行迁移的升级警告；Step 8 systemd restart 后执行 `is-active` 验证
- **gateway/scripts/deploy.sh**: Step 5 venv 检查改为检测 `venv/bin/pip` 是否存在（原仅检查目录，不完整时跳过重建导致失败）；Step 7 systemd restart 后执行 `is-active` 验证
- **web/scripts/deploy.sh**: Step 7 systemd restart 后执行 `is-active` 验证
- **scripts/deploy-all.sh**: 新增 `--mode`/`--domain` 命令行参数支持非交互式执行（CI/CD 友好）；Step 5 对账阶段新增 LLM API Key 占位值检测（场景 A/B 分别检测）和 `POSTGRES_PASSWORD` 弱密码检测
- **升级行为澄清**: 二次部署时 `.env` 受 `rsync --exclude` 保护、`POSTGRES_PASSWORD` 因值已非弱密码跳过生成、`create_all()` 幂等但不迁移列变更

### 2026-04-06: Sprint 4 批量实现
- 迁移 `main.py` 从废弃的 `@app.on_event` 到 `lifespan` context manager
- 创建 `vector_search.py` — 使用 TF-IDF baseline（不需要 API Key），预留 embedding 接口
- 创建 `doc_retrieval.py` — 第 3 个 Agent，加入 SCENARIO_AGENT_MAP
- 实现 3 个 Tool Use 函数（`extract_timeline_events`, `fetch_raw_line_context`, `search_fota_stage_transitions`）
- RCA Synthesizer 增加 `_validate_citations()` 引用 ID 断言验证
- 创建 `semantic_cache.py` 的 SHA-256 精确匹配模式
- 创建 `api/feedback.py`（5 个端点）和 `api/metrics.py`（Prometheus 格式）
- 创建 `evaluation.py` 评测框架（5 个标准 case，5 维评分）
- 创建 `doc_chunker.py` 支持 PDF/文本切块（3 种策略）
- 演示日志扩充至 5 份，Jira 工单扩充至 10 个
- `vitest.config.ts` 添加覆盖率 thresholds（branches≥70%, functions≥70%, lines≥80%, statements≥80%）
- 总体进度 80% → 93%

### 2026-05-03: 单元测试全面补全
- **后端新增 4 个测试文件（63 个用例）**：`test_redaction.py`（17）、`test_chain_log.py`（16）、`test_tool_functions.py`（13）、`test_semantic_cache.py`（17）
- 覆盖此前零测试的核心模块：`common/redaction.py`（VIN/手机号/车牌脱敏 + async 装饰器）、`common/chain_log.py`（trace_id 管理 + step_timer）、`services/tool_functions.py`（workspace 文件读写 + todo 状态）、`services/semantic_cache.py`（SHA-256 精确缓存 MISS/HIT/UPSERT/stats）
- **前端新增 7 个测试文件（16 个用例）**：sessions、sessions/[sessionId]、session-title、upload-log、bundle-status/[bundleId]、bundle-events/[bundleId]、bundle-logs/[bundleId] 全部 100% 覆盖
- **修复 patch 路径**：`tool_functions.py` 中 `workspace_manager` 是延迟导入（函数内 `from services.workspace_manager import ...`），patch 目标为 `services.workspace_manager.workspace_manager` 而非 `services.tool_functions.workspace_manager`
- **修复 JSDOM/undici 兼容性**：`upload-log` 测试中 `new NextRequest(body=FormData)` 会触发 undici webidl 断言；改为 mock `request.formData()` 方法绕过
- **后端总量**：145 → **208 passed**；**前端总量**：185 → **201 passed**；前端覆盖率 statements 84.7% / branches 74.4% / lines 87.8%，全部高于红线

### 2026-05-03: Bundle-Agent 断链修复
- **问题根因**: 用户上传日志包后发起对话，`LogAnalyticsAgent._load_logs()` 固定读 `data/logs/` mock 数据，与上传的真实日志完全断链。
- **修复 4 处**:
  1. `backend/agents/log_analytics.py`: 新增 `_load_logs_from_bundle(bundle_id, keywords)` — 调用 `/api/bundles/{id}` 获取时间范围，再调用 `/api/bundles/{id}/logs` 拉 NDJSON，解析为可读文本，按关键词过滤，多级 fallback 到 `data/logs/`
  2. `backend/agents/orchestrator.py`: `orchestrate()` 新增 `bundle_id` 参数，若未传则自动从 `conversation_history` 中扫描最近一条 `systemKind: upload_summary` 提取；`_run_agent` 将 `bundle_id` 注入 `agent_context`
  3. `backend/main.py`: `/chat` 端点从 body 读取可选 `bundle_id`，传给 `orchestrate()`
  4. `web/src/app/page.tsx`: `handleSend()` 提取当前会话消息中最近 `uploadSummaries` 的 `bundleId`，附加到 fetch `/api/chat` 请求体；`api/chat/route.ts` 新增 `bundleId` 校验
- **config.py**: 新增 `BACKEND_BASE_URL: str = "http://localhost:8000"` 供 Agent 内部调用使用
- **新增测试**: `backend/tests/test_log_analytics_bundle.py`（11 个用例）覆盖 bundle 加载成功、404 fallback、无时间范围 fallback、网络异常 fallback、关键词过滤、execute() 路由、orchestrator bundle_id 注入
- **后端总量**: 208 → **219 passed**；前端测试无变化（201 passed）
- **安全修复**: `ChatMessage.tsx` 新增 `escapeHtml()`/`sanitizeUrl()` 防 XSS；`chat/route.ts` 添加输入验证；`next.config.ts` 添加 5 个安全响应头；`main.py` CORS 收窄为 `ALLOWED_ORIGINS` 环境变量
- **依赖**: `npm audit fix` 修复 Vite 3 个 HIGH CVE；`package.json overrides` 强制 postcss ≥ 8.5.10，`npm audit` 输出 0 vulnerabilities
- **scripts/dev.sh**: 一键启动脚本，启动前自动清理旧进程（SIGTERM→SIGKILL），按 `DEPLOYMENT_MODE` 智能决定是否启动 LiteLLM Gateway
- **CI 修复**: `ci.yml` 补全 `POSTGRES_DB` 等环境变量；新增 Redis 7 service；flake8 增加 `--exclude=venv,.venv`
- **scripts/test-ci.sh**: 本地 CI 模拟脚本，**发 PR 前必须先跑**，看到绿色通过提示再推送
- **Copilot Skills**: `.agents/skills/` 25 个 + `.github/agents/` 3 个 Persona + `.github/copilot-instructions.md`

### 2026-05-03: log_pipeline 上传格式扩展 + 兼容性修复
- **新增 `.rar` 上传支持**：`log_pipeline/ingest/extractor.py` 新增 `import rarfile` 和 `_extract_rar()` 方法，依赖系统 `/usr/bin/unrar`；`_NESTED_ARCHIVE_SUFFIXES` 追加 `.rar`（支持 RAR 内嵌归档递归展开）；`requirements.txt` 新增 `rarfile==4.2`
- **新增裸文件上传支持**（`.log / .txt / .dlt`）：`_extract_into` else 分支改为调用 `_extract_plain()`（原来直接抛 ValueError）；HTTP 白名单同步扩展，错误码改为 `UNSUPPORTED_FORMAT`
- **修复 `_extract_plain` UUID 前缀 Bug**：上传文件保存为 `{uuid32}__原始名`，`_extract_plain` 原来直接以磁盘名为 `relative_path`，导致分类器（如 `fota*.log`）无法命中；新增 `_UPLOAD_PREFIX_RE = re.compile(r'^[0-9a-f]{32}__')` 剥离前缀后使用原始文件名
- **冒烟验证**：对真实 `fota_log.rar`（221MB / 273 成员）验证，路径含中文、嵌套 zip 自动展开，共输出 254 文件；全部后端 219 个测试通过
### 2026-05-03: LLM 依赖模块集成测试全面补全
- **后端新增 8 个测试文件（131 个用例）**：`test_session_title.py`（18）、`test_rca_synthesizer.py`（19）、`test_doc_chunker.py`（15）、`test_feedback_api.py`（14）、`test_jira_knowledge.py`（15）、`test_doc_retrieval.py`（9）、`test_evaluation.py`（19）、`test_log_analytics_bundle.py`（11+，含 bundle 加载成功/404/超时/关键词过滤/orchestrator 注入）；log_pipeline 追加 `test_http_upload.py`（10）
- **覆盖 LLM 依赖模块**：`agents/jira_knowledge.py`（embed 分支 fallback + keyword 搜索）、`agents/doc_retrieval.py`（vector_service mock + 相似度置信度映射）、`services/evaluation.py`（5 维评分 + load_eval_set + run_eval 报告）
- **关键 mock 策略**：LLM 延迟导入需 patch `"services.llm.chat_completion"`；vector_service 使用 `patch.object(agent, "_load_documents", ...)`
- **后端总量**：219 → **350 passed**

### 2026-05-03: 代码审查安全加固（全量 diff 审查）
- **[C-1] 修复 Bundle 集成 Day-1 Bug**：`log_analytics._load_logs_from_bundle` 读取了不存在的字段 `valid_time_range`（实际 API 返回 `valid_time_range_by_controller: {ctrl: {start, end}}`），导致 Bundle 日志永远 fallback 到 mock；同步修正 `test_log_analytics_bundle.py` 测试 mock
- **[C-2] 修复路径遍历漏洞**：`_should_skip()` 新增 `".." in parts` 和绝对路径检测，阻断 RAR/ZIP/TAR 归档中的任意文件写入攻击
- **[I-1] `main.py` bundle_id UUID 校验**：API 边界处增加 `re.fullmatch` UUID 格式验证，非法值返回 400，防止路径注入/SSRF
- **[I-2] orchestrator 历史提取 bundle_id 防伪造**：从 `conversation_history` 自动提取的 `bundleId` 增加 UUID 格式正则校验，拒绝非法格式
- **[I-3] log_resp 移入 with 块**：`raw_text = log_resp.text` 在 `async with` 关闭前捕获，防止未来切换流式读取时崩溃
- **[I-4] save_embed_index 原子写入**：`.partial + os.replace()` 模式，防止进程崩溃导致索引文件损坏
- **[I-5] route.ts bundleId UUID 校验**：前端 API 路由增加长度上限（36）和 UUID 正则校验
- **[S-1] http.py 常量提升**：`_PLAIN_SUFFIXES`/`_ARCHIVE_SUFFIXES` 移至模块级 `frozenset`，避免每次请求重建
- **测试总量**：350 passed（后端），201 passed（前端），全量无回归

### 2026-05-07: log_pipeline 时间范围修复 + EventDigest UI + time_hint 功能
- **修复 catalog.py 时间范围计算**：`valid_time_range_by_controller()` SQL CASE 表达式 `ELSE NULL` 改为 `ELSE valid_ts_min/max`，修复 FOTA Java 文本日志（`clock_offset=NULL`、时间戳已是 wall-clock）被排除在有效时间范围之外的问题
- **修复 range_query.py `_file_overlaps()`**：`clock_offset is None` 时不再直接返回 `False`，改为检查 `valid_ts_min/max` 是否存在并直接比较；`unsynced_files` 条件增加 `valid_ts_min is None`，防止有效文件被双重发送
- **EventDigest 上传摘要卡**：日志包处理完成后聊天界面自动展示事件摘要（最近重启、最后严重故障、FOTA 结果）
  - `web/src/lib/types.ts`：新增 `EventDigestItem`、`EventDigest` 接口；`UploadSummary` 扩展可选 `eventDigest` 字段
  - `web/src/app/page.tsx`：bundle 处理完成后调用 `/api/bundle-events/{id}?limit=500`，计算 digest 附加到 `uploadSummaries`
  - `web/src/components/UploadSummaryCard.tsx`：新增 `EventDigestPanel` 组件，渲染 🔄重启 / 🔴故障 / ✅❌FOTA结果行
- **time_hint 可选时间窗口缩窄**：
  - `backend/agents/log_analytics.py`：新增 `_parse_time_hint()` 函数，解析中文时间描述（月日、凌晨/上午/下午/晚上等时段词、精确小时），以 bundle 有效时间范围为日历锚点，返回 `(start, end)` Unix 时间戳对，无法解析返回 `None`
  - `LogAnalyticsAgent.tool_schema()` 覆盖：在 LLM 函数调用 schema 中暴露可选 `time_hint` 字段
  - `_load_logs_from_bundle()` 新增 `time_hint` 参数，解析成功时缩窄查询窗口，失败时退化为全量
  - `backend/agents/orchestrator.py`：`_run_agent()` 从 tool call args 提取 `time_hint` 注入 `agent_context`
- **测试总量**：344 passed（后端），201 passed（前端），全量无回归