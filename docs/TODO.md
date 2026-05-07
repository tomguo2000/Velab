# Velab 项目任务清单

> **最后更新**: 2026-05-07（log_pipeline 时间范围修复 + EventDigest UI + time_hint 功能）
> **当前阶段**: Sprint 6 进行中
> **下一阶段**: Sprint 6 - 真实 Jira 数据同步、权限体系与操作审计、生产部署

---

## ✅ 已完成任务

### 1. 基础设施与部署配置 (P0) - 100% 完成

- [x] **Backend 部署配置完整**
  - [x] 创建 `backend/scripts/deploy.sh` - 生产环境自动化部署
  - [x] 创建 `backend/scripts/start-dev.sh` - 开发环境启动
  - [x] 创建 `backend/systemd/fota-backend.service` - systemd 服务
  - [x] 创建 `backend/nginx/backend.conf` - Nginx 反向代理
  - [x] 完善 `backend/.env.example` - 环境变量配置
  - [x] 创建 `backend/README.md` - 完整部署文档

- [x] **Gateway 部署配置完整**
  - [x] 补充 `gateway/config.yaml` - Key Pool 配置
  - [x] 创建 `gateway/scripts/deploy.sh` - 生产环境自动化部署
  - [x] 创建 `gateway/scripts/start.sh` - 开发环境启动
  - [x] 创建 `gateway/scripts/validate_config.sh` - 配置验证脚本
  - [x] 创建 `gateway/systemd/litellm.service` - systemd 服务
  - [x] 创建 `gateway/nginx/litellm.conf` - Nginx 反向代理（含 Cloudflare SSL）
  - [x] 完善 `gateway/.env.example` - 环境变量配置（含多 Key Pool）
  - [x] 完善 `gateway/README.md` - 完整部署文档（含自动部署和配置验证说明）

- [x] **统一部署脚本**
  - [x] 创建 `scripts/deploy-all.sh` - 单机开发环境一键部署

- [x] **项目文档**
  - [x] 创建 `docs/AI专家项目分析报告.md` - 项目深度分析
  - [x] 创建 `docs/部署配置完整性检查报告.md` - 配置完整性检查
  - [x] 创建 `gateway/gateway功能检查报告.md` - Gateway 功能检查

- [x] **代码注释完善 (2026-04-02 新增)**
  - [x] 后端 Python 文件头部注释和方法注释
    - [x] `backend/config.py` - 配置管理模块注释
    - [x] `backend/agents/base.py` - Agent 基类架构注释
    - [x] `backend/main.py` - FastAPI 服务入口注释
  - [x] 前端 TypeScript 文件头部注释和方法注释
    - [x] `web/src/app/api/chat/route.ts` - API 路由代理层注释
    - [x] `web/src/app/page.tsx` - 主页面组件注释
    - [x] `web/src/components/ChatMessage.tsx` - 消息组件注释
  - [x] 创建完整项目文档 `claude.md`
  - [x] 更新主 README 和各组件 README
  - [x] 更新 .gitignore（添加 Log/ 和 AI 文档忽略）
  - [x] 删除冲突的旧版 `scripts/deploy.sh`
  - [x] 全面代码审核（语法、类型、逻辑检查）
  - [x] 脚本环境检查和提示完善性审核

- [x] **前端测试框架完善 (2026-04-03 新增)**
  - [x] 配置 Vitest 4.1.2 测试框架
  - [x] 配置 React Testing Library 16.3.2
  - [x] 配置 MSW 2.12.14 用于 API 模拟
  - [x] 添加所有组件测试（ChatMessage, ThinkingProcess, InputBar, Header, WelcomePage, FeedbackButtons）
  - [x] 添加集成测试（page.tsx）
  - [x] 添加 API 路由测试（/api/chat）
  - [x] 配置测试覆盖率目标（分支≥70%, 函数≥70%, 行≥80%, 语句≥80%）
    - ✅ `vitest.config.ts` 中已配置 `thresholds` 强制校验 (2026-04-06 修复)
  - [x] 创建完整测试文档 `web/README_TESTING.md`
  - [x] 更新文档以反映测试框架变更

- [x] **离线数据预处理管线 (P0) - 100% 完成 (2026-04-04 新增)**
  - [x] **数据库 Schema 创建**
    - [x] `cases` 表（案件记录）
    - [x] `raw_log_files` 表（原始日志文件元数据）
    - [x] `diagnosis_events` 表（诊断事件详情）
    - [x] `confirmed_diagnosis` 表（已确认诊断缓存）
  - [x] **Parser Service 实现（7个解析器）**
    - [x] `parser_android` - Android logcat 解析
    - [x] `parser_fota` - FOTA 文本日志解析
    - [x] `parser_kernel` - kernel / tombstone / ANR 解析
    - [x] `parser_mcu` - MCU 日志解析
    - [x] `parser_dlt` - AUTOSAR DLT 格式解析
    - [x] `parser_ibdu` - iBDU 电源管理日志解析
    - [x] `parser_vehicle_signal` - 车辆信号导出文件解析
  - [x] **Time Alignment Service 实现**
    - [x] 锚点事件识别（Android启动/关键系统事件）
    - [x] 时钟偏移计算（加权平均拟合）
    - [x] `normalized_ts` 生成
    - [x] 三级降级策略（高/中/低置信度）
  - [x] **Event Normalizer 实现**
    - [x] 语义归一化（统一事件描述）
    - [x] 降噪（过滤冗余事件）
    - [x] 事件分类（ERROR/WARNING/INFO等）
  - [x] **数据库集成层**
    - [x] SQLAlchemy 2.0+ ORM模型
    - [x] 同步/异步数据库连接管理
    - [x] 批量操作优化（bulk_insert/upsert）
  - [x] **API接口层（15个端点）**
    - [x] Cases API（4个端点）- 案件管理（创建/查询/列表/删除）
    - [x] Logs API（4个端点）- 日志文件管理（上传/查询/列表/删除）
    - [x] Parse API（3个端点）- 解析任务提交/查询/时间对齐
    - [x] Events API（4个端点）- 事件查询/单个/摘要/导出
  - [x] **任务队列集成**
    - [x] Arq异步任务队列（基于Redis）
    - [x] Worker实现（parse_logs_task）
    - [x] 任务客户端（提交/查询/取消）
  - [x] **API测试（34个测试）**
    - [x] Cases API单元测试（9个测试）
    - [x] Parse API单元测试（8个测试）
    - [x] Events API单元测试（13个测试）
    - [x] 集成测试（4个测试）
  - [x] 创建完整实施报告 `docs/P0任务实施进度报告.md`

- [x] **MVP核心功能实现 (2026-04-04 新增)** ✅
  - [x] **RCA Synthesizer Agent 实现**
    - [x] 创建 `backend/agents/rca_synthesizer.py` - RCA综合分析Agent
    - [x] 多Agent结果聚合逻辑
    - [x] 综合置信度计算
    - [x] 执行摘要和建议生成
  - [x] **Orchestrator 增强**
    - [x] 自动调用RCA Synthesizer
    - [x] 完整证据链追溯
    - [x] 多Agent并行执行
  - [x] **演示数据创建**
    - [x] `backend/data/logs/fota_upgrade_failure_20250911.log` - FOTA升级失败日志
    - [x] `backend/data/jira_mock/tickets.json` - 4个历史Jira工单
    - [x] `backend/data/jira_mock/documents.json` - 3份技术文档
  - [x] **Agent注册修复**
    - [x] 更新 `backend/agents/__init__.py` - 确保所有Agent正确注册
  - [x] **端到端测试**
    - [x] 多Agent协作测试通过
    - [x] RCA综合分析测试通过
    - [x] Fallback机制测试通过
    - [x] 证据链完整性验证通过
  - [x] 创建完整MVP实施报告 `docs/MVP实施总结报告.md`

---

## 🚧 进行中任务

### 2. 后端核心逻辑实现 (P1) - **100% 完成** ✅

- [x] **基础框架搭建**
  - [x] FastAPI 应用入口 (`main.py`，已迁移 lifespan API）
  - [x] Agent 注册机制 (`agents/base.py`)
  - [x] Orchestrator 编排器 (`agents/orchestrator.py`)
  - [x] LLM 服务抽象层 (`services/llm.py`)
  - [x] 结构化日志 (`common/chain_log.py`)

- [x] **Log Analytics Agent MVP实现** ✅
  - [x] 基础日志分析功能（使用Mock数据）
  - [x] 从本地文件读取日志
  - [x] 异常模式识别（Mock 硬编码，待 LLM 替换）
  - [x] 实现时间窗口裁剪逻辑（`services/tool_functions.py:clip_log_by_time_window`）(2026-04-06)
  - [x] 接入真实 LLM 推理（`_llm_analyze()` 实现，`AGENTS_USE_LLM=true`，API Key 已配置）
  - [x] 实现 Tool Use：`extract_timeline_events` (2026-04-06)
  - [x] 实现 Tool Use：`fetch_raw_line_context` (2026-04-06)
  - [x] 实现 Tool Use：`search_fota_stage_transitions` (2026-04-06)

- [x] **Jira Knowledge Agent MVP实现** ✅
  - [x] 基础知识库检索（使用Mock数据）
  - [x] 历史工单匹配（从 `backend/data/jira_mock/tickets.json` 读取，已扩展至 10 个工单）
  - [x] 技术文档检索（从 `backend/data/jira_mock/documents.json` 读取）
  - [x] 创建 `services/vector_search.py` — TF-IDF baseline 向量检索服务 (2026-04-06)
  - [x] 实现 Tool Use：`vector_search_jira_issues`（TF-IDF 模式）(2026-04-06)
  - [x] 实现 Tool Use：`search_documents`（TF-IDF 模式）(2026-04-06)
  - [x] 补充 FOTA 典型故障案例数据（已扩展至 10 个 Mock 工单）(2026-04-06)
  - [x] 切换 vector_search 到 embedding 模式（`_index_with_embeddings`/`_search_with_embeddings`/`async_search_jira_issues`/`async_search_documents` 实现，`save_embed_index`/`load_embed_index` 持久化）(2026-05-03)
  - [x] 单元测试覆盖 `common/redaction.py`、`common/chain_log.py`、`services/tool_functions.py`、`services/semantic_cache.py`（63 个新测试用例，后端合计 **208 passed**）(2026-05-03)

- [x] **Doc Retrieval Agent 实现** ✅ (2026-04-06)
  - [x] 创建 `backend/agents/doc_retrieval.py` — 文档检索 Agent
  - [x] TF-IDF 文本匹配检索
  - [x] 加载 `data/docs/index.json` 文档索引（6 份技术文档）
  - [x] 内置 5 份保底文档
  - [x] 注册到 SCENARIO_AGENT_MAP（fota-diagnostic, fota-jira, ces-demo）

- [x] **RCA Synthesizer 实现** ✅
  - [x] 多路证据汇总逻辑
  - [x] 置信度量化计算
  - [x] 引用 ID 断言验证（完整性/一致性/重复/孤立检查）(2026-04-06)
  - [x] 执行摘要生成
  - [x] 建议生成

---

## ✅ 已完成 - 前端

### 3. 前端交互功能开发 (P1) - 100% 完成 ✅

- [x] **基础UI框架** ✅
  - [x] 聊天式诊断页面（[`web/src/app/page.tsx`](../web/src/app/page.tsx)）
  - [x] 问题输入框（[`web/src/components/InputBar.tsx`](../web/src/components/InputBar.tsx)）
  - [x] 对话历史管理（内存维护，支持场景切换清空）
  - [x] Demo模式/场景切换（[`web/src/components/Header.tsx`](../web/src/components/Header.tsx)）

- [x] **SSE流式渲染** ✅
  - [x] 基础SSE流式处理（[`web/src/app/page.tsx`](../web/src/app/page.tsx:98-280)）
  - [x] 实时消息展示
  - [x] `<<<THINKING>>>` 标记内容灰色折叠框展示（[`web/src/components/ChatMessage.tsx`](../web/src/components/ChatMessage.tsx:55-65)）
  - [x] Markdown 格式诊断报告渲染（表格 + 置信度标签）（[`web/src/components/ChatMessage.tsx`](../web/src/components/ChatMessage.tsx:68-90)）

- [x] **执行状态 Timeline** ✅
  - [x] ThinkingProcess组件（[`web/src/components/ThinkingProcess.tsx`](../web/src/components/ThinkingProcess.tsx)）
  - [x] 展示 Orchestrator 调度各 Agent 的动态过程
  - [x] Agent 状态实时更新（Analyzing... → Done）
  - [x] 步骤折叠/展开功能

- [x] **引用来源面板** ✅
  - [x] SourcePanel组件（[`web/src/components/SourcePanel.tsx`](../web/src/components/SourcePanel.tsx)）
  - [x] 点击引用来源弹出浮窗或打开链接
  - [x] 支持日志/Jira/文档/PDF类型
  - [x] 展示对应的日志片段或 Jira 描述

- [x] **FOTA专用预设问题** ✅
  - [x] 更新为FOTA诊断专用问题（[`web/src/lib/types.ts`](../web/src/lib/types.ts:71-92)）
  - [x] 4个诊断场景：升级失败分析、历史案例查询、iCGM挂死分析、MPU校验失败

### 4. 数据与演示场景准备 (P2) - 90% 完成

- [x] **演示日志集** ✅ (2026-04-06 完成)
  - [x] 在 `backend/data/jira_mock/` 放置 Mock 数据（tickets.json + documents.json）
  - [x] 在 `backend/data/logs/` 放置 5 份 FOTA 故障日志样本：
    - [x] `fota_upgrade_failure_20250911.log` — 基础升级失败
    - [x] `icgm_emmc_timeout_20250915.log` — iCGM eMMC 写入超时 + 回退 (2026-04-06)
    - [x] `network_interrupt_download_20251003.log` — 网络中断下载校验失败 (2026-04-06)
    - [x] `ecu_dependency_chain_failure_20251120.log` — ECU 依赖链断裂 (2026-04-06)
    - [x] `battery_drain_abort_20251208.log` — 电池电量不足紧急中止 (2026-04-06)

- [x] **FOTA专用场景引导词** ✅
  - [x] 前端预设问题已更新为FOTA诊断专用
  - [x] 4个预设问题

- [x] **Jira 工单数据（Mock）** ✅
  - [x] 创建Mock历史 Jira 工单（10个，2026-04-06 从 4→10 扩充）
  - [ ] 同步真实历史 Jira 工单
  - [ ] 向量化入库（需 embedding API Key）

- [x] **技术文档数据** ✅ (2026-04-06)
  - [x] 创建 `data/docs/index.json` — 6 份技术文档索引
  - [x] 创建 `services/doc_chunker.py` — PDF/文本文档切块服务
  - [x] 支持 3 种切块策略（段落感知/固定长度/滑动窗口）
  - [x] 支持 PDF 提取（pdfplumber / PyPDF2）
  - [ ] 向量化入库（需 embedding API Key）

### 5. 评测与验收 (P2) - 70% 完成

- [x] **基准测试集建设** ✅ (2026-04-06)
  - [x] 构建 5 个标准 case（`services/evaluation.py: BUILTIN_EVAL_CASES`）
  - [x] 标注期望根因、关键词、ECU、FOTA 阶段、置信度

- [x] **评测指标框架** ✅ (2026-04-06)
  - [x] 关键词命中率（权重 25%）
  - [x] ECU 识别准确率（权重 20%）
  - [x] FOTA 阶段检测率（权重 20%）
  - [x] 根因相关度（权重 25%）
  - [x] 置信度匹配（权重 10%）
  - [x] 加权总分 + 通过阈值（≥0.6）

- [ ] **人工评审**
  - [ ] 领域专家评审结论是否靠谱
  - [ ] 证据是否站得住
  - [ ] 建议是否可执行

### 6. 服务增强 (P1) - 100% 完成 (2026-04-06 新增)

- [x] **语义缓存服务** (`services/semantic_cache.py`)
  - [x] 精确哈希匹配（SHA-256）
  - [x] 缓存 UPSERT / 失效 / 统计
  - [x] TTL 过期清理

- [x] **诊断反馈 API** (`api/feedback.py`, 5 个端点)
  - [x] POST /api/feedback — 提交确认/拒绝/部分确认
  - [x] GET /api/feedback/case/{id} — 按案件查询
  - [x] GET /api/feedback/{id} — 单条详情
  - [x] GET /api/feedback — 列表（可按状态过滤）
  - [x] GET /api/feedback/stats/summary — 统计摘要

- [x] **监控指标** (`api/metrics.py`)
  - [x] GET /api/metrics — Prometheus text format 导出
  - [x] GET /api/metrics/json — JSON 格式摘要
  - [x] 计数器 / 直方图 / 仪表盘
  - [x] 数据库连接池状态

### 7. log_pipeline 整车日志管线 M1-M6 (2026-04-10 ~ 2026-05-03 完成) ✅

> 完整替换旧 `services/parser/` 方案，实现压缩包摄入 → 分类 → 解码 → 时间对齐 → 预扫描 → 存储 → 查询全链路

- [x] **M1 摄入层** (`log_pipeline/ingest/`)
  - [x] `pipeline.py` — 压缩包解压、文件发现、完整管线入口
  - [x] `classifier.py` — 按控制器分类归档（tbox/android/kernel/fota/dlt/mcu/ibdu 等）
  - [x] `extractor.py` — 解压 zip/tar.gz/tar/rar，直传 .log/.txt/.dlt 裸文件；UUID 上传前缀自动剥离（2026-05-03 新增 rar + plain 支持）

- [x] **M2 解码层** (`log_pipeline/decoders/`)
  - [x] `android_logcat.py` — Android logcat 文本解码
  - [x] `dlt.py` — AUTOSAR DLT 二进制解码（输出 UTF-8 文本）
  - [x] `fota_text.py` — FOTA 升级文本日志解码
  - [x] `ibdu.py` — iBDU 电源管理日志解码
  - [x] `kernel.py` — kernel/tombstone/ANR 解码
  - [x] `mcu_text.py` — MCU 文本日志解码
  - [x] `tbox_text.py` — Tbox 日志解码（统一时钟源）
  - [x] `stage.py` — 多解码器管线阶段封装

- [x] **M3 时间对齐层** (`log_pipeline/alignment/`)
  - [x] `time_aligner.py` — 以 tbox 为统一时钟源，计算各控制器偏移
  - [x] `crash_heuristic.py` — 崩溃时间戳启发式识别
  - [x] `unsynced_segments.py` — 未同步段（1970/2000 伪时间戳）标注与保留
  - [x] `stage.py` — 对齐管线阶段封装
  - [x] 配置驱动：`config/anchor_rules.yaml`（锚点事件规则）

- [x] **M4 预扫描层** (`log_pipeline/prescan/`)
  - [x] `prescanner.py` — 单遍预扫描，抽取重要事件、采集锚点、构建文件级时间索引
  - [x] `rule_engine.py` — YAML 外置规则引擎（`config/event_rules.yaml`）
  - [x] `stage.py` — 预扫描管线阶段封装

- [x] **M5 索引层** (`log_pipeline/index/`)
  - [x] `file_index.py` — 紧凑二进制桶索引（每记录 24B，`.idx` 文件格式）

- [x] **M6 存储层** (`log_pipeline/storage/`)
  - [x] `catalog.py` — bundle/file 元数据（含 clock_offset、unsynced_ranges、bucket_index_path）
  - [x] `eventdb.py` — 重要事件数据库（唯一入库的日志衍生数据）
  - [x] `filestore.py` — 磁盘文件存储管理

- [x] **查询层** (`log_pipeline/query/`)
  - [x] `range_query.py` — 按统一时间段查询，支持全量/精简格式
  - [x] `slim_filter.py` — 动态三级精简过滤（`config/slim_rules.yaml`）

- [x] **HTTP API** (`log_pipeline/api/http.py`) — `/api/bundles/*` 端点
  - [x] POST /api/bundles — 上传 zip/tar.gz/tar/rar 或裸文件（.log/.txt/.dlt），触发异步摄入管线（2026-05-03 新增 rar 及裸文件支持）
  - [x] GET /api/bundles/{id}/status — 查询处理进度
  - [x] GET /api/bundles/{id}/events — 查询重要事件
  - [x] GET /api/bundles/{id}/logs — 按时间段查询日志

- [x] **测试覆盖** (`log_pipeline/tests/`，12 个文件，107 个测试函数)
  - [x] test_alignment, test_catalog, test_classifier, test_decoders
  - [x] test_eventdb, test_extractor, test_file_index, test_filestore
  - [x] test_metrics, test_prescan, test_query, test_rule_engine

### 8. 前端日志上传工作流 (2026-04-10 ~ 2026-05-03 完成) ✅

- [x] **会话持久化** (`web/src/components/SessionSidebar.tsx`)
  - [x] 侧边栏展示历史会话列表
  - [x] 支持新建/切换/删除会话
  - [x] API：GET/POST/DELETE `/api/sessions`

- [x] **日志包上传摘要** (`web/src/components/UploadSummaryCard.tsx`)
  - [x] 上传进度实时显示
  - [x] 解析状态轮询（`lib/bundleStatus.ts`）
  - [x] 展示摄入结果摘要（文件数量、事件数量）
  - [x] 错误状态展示

- [x] **新增前端 API 路由**
  - [x] `app/api/upload-log/route.ts` — 代理日志包上传至后端
  - [x] `app/api/bundle-status/[bundleId]/route.ts` — 查询处理状态
  - [x] `app/api/bundle-events/[bundleId]/route.ts` — 查询事件列表
  - [x] `app/api/bundle-logs/[bundleId]/route.ts` — 查询日志内容
  - [x] `app/api/sessions/route.ts` + `[sessionId]/route.ts` — 会话 CRUD
  - [x] `app/api/parse-status/[taskId]/route.ts` — 解析任务状态
  - [x] `app/api/session-title/route.ts` — 会话标题生成

- [x] **工具库**
  - [x] `lib/bundleStatus.ts` — Bundle 状态轮询客户端
  - [x] `lib/sseParse.ts` — SSE 流解析工具

- [x] **新增测试** (UploadSummaryCard, SessionSidebar, bundleStatus, sseParse)
  - [x] 7 个原零覆盖 API 代理路由补测（sessions, session-title, upload-log, bundle-status, bundle-events, bundle-logs，前端合计 **201 passed**）(2026-05-03)

### 9. 安全加固 (2026-05-03 完成) ✅

- [x] **前端 XSS 防护** (`web/src/components/ChatMessage.tsx`)
  - [x] 新增 `escapeHtml()` 函数，防止 HTML 注入
  - [x] 新增 `sanitizeUrl()` 函数，过滤 `javascript:` 等危险协议

- [x] **API 输入验证** (`web/src/app/api/chat/route.ts`)
  - [x] 请求体 schema 验证（消息列表非空、字符串长度限制）

- [x] **安全响应头** (`web/next.config.ts`)
  - [x] `X-Content-Type-Options: nosniff`
  - [x] `X-Frame-Options: DENY`
  - [x] `X-XSS-Protection: 1; mode=block`
  - [x] `Referrer-Policy: strict-origin-when-cross-origin`
  - [x] `Permissions-Policy: camera=(), microphone=(), geolocation=()`

- [x] **CORS 收窄** (`backend/main.py`, `backend/config.py`)
  - [x] 新增 `ALLOWED_ORIGINS` 字段，从 `*` 改为环境变量白名单

- [x] **依赖漏洞修复** (`web/package.json`)
  - [x] `npm audit fix` — 修复 Vite 3 个 HIGH CVE
  - [x] `overrides.postcss` ≥ 8.5.10 — 修复传递依赖漏洞
  - [x] 最终 `npm audit` 输出 0 vulnerabilities

### 10. 开发工具链与 CI (2026-04-10 ~ 2026-05-03 完成) ✅

- [x] **一键启动脚本** (`scripts/dev.sh`)
  - [x] 启动前自动清理占用端口的旧进程（SIGTERM→SIGKILL）
  - [x] 按 `DEPLOYMENT_MODE` 智能决定是否启动 LiteLLM Gateway
  - [x] 并发启动 Backend(8000) + Frontend(3000) + 可选 Gateway(4000)

- [x] **本地 CI 模拟脚本** (`scripts/test-ci.sh`)
  - [x] PostgreSQL + Redis 前置连接检查（自动创建测试库）
  - [x] flake8 语法检查（`--exclude=venv,.venv`）
  - [x] pytest 完整测试（完整 CI 环境变量复刻）
  - [x] ESLint + Vitest 覆盖率验证
  - [x] 通过后打印 `✅ 本地 CI 全部通过，可以提交 PR！`

- [x] **GitHub Actions CI 修复** (`.github/workflows/ci.yml`)
  - [x] 新增 `redis:7` service（health-check 等待就绪）
  - [x] 补全 backend 测试环境变量（POSTGRES_DB/USER/PASSWORD/HOST/PORT）
  - [x] flake8 加 `--exclude=venv,.venv`

- [x] **Copilot Skills 体系** (`.agents/skills/`, 25 个 Skills)
  - [x] 通用技能：api-design, browser-testing, ci-cd, code-review, etc.
  - [x] Velab 专属：fota-expert-assistant, velab-devops-operator, velab-frontend-expert, velab-qa-engineer
  - [x] AI Personas：`.github/agents/` code-reviewer, security-auditor, test-engineer
  - [x] `.github/copilot-instructions.md` 编码规范

- [x] **脚本工具链重构** (`backend/scripts/`)
  - [x] 抽取 `lib/common.sh` 跨平台公共库
  - [x] 统一各部署脚本日志格式与错误处理

---

## 🎯 Sprint 规划

### Sprint 1（已完成）✅
- ✅ 基础架构搭建
- ✅ 部署配置完整
- ✅ 文档完善

### Sprint 2（已完成）✅
- ✅ 离线预处理管线（Parser + Time Alignment + Event Normalizer）- 已完成
- ✅ 数据库集成层（ORM + 批量操作）- 已完成
- ✅ API接口层（15个端点）- 已完成
- ✅ 任务队列集成（Arq + Redis）- 已完成
- ✅ API测试（34个测试）- 已完成
- ✅ MVP核心功能（Log Analytics + Jira Knowledge + RCA Synthesizer）- 已完成
- ✅ 演示数据准备（日志 + Jira工单 + 技术文档）- 已完成
- ✅ 端到端测试 - 已完成
- ✅ 语义缓存实现 - 完成（`services/semantic_cache.py`，精确哈希匹配模式）

### Sprint 3（已完成）✅
- ✅ 前端 UI 开发（100%完成）
- ✅ Agent 执行状态面板（ThinkingProcess组件）
- ✅ RCA 报告展示 + 引用来源跳转（SourcePanel组件）
- ✅ FOTA专用预设问题
- ✅ Markdown渲染增强（置信度标签、THINKING折叠）
- ✅ 已确认诊断缓存 + 反馈闭环（`api/feedback.py`，5个端点）

### Sprint 4（已完成）✅
- ✅ Tool Use 实现（3个工具函数 + 时间窗口裁剪）
- ✅ 向量检索服务（TF-IDF baseline）
- ✅ Doc Retrieval Agent
- ✅ 评测框架（5个标准case + 5维评分）
- ✅ 文档切块服务
- ✅ 语义缓存 + 反馈闭环 API
- ✅ 监控指标 API
- ✅ 引用 ID 断言验证
- ✅ 演示日志扩充（5份）+ Jira工单扩充（10个）

### Sprint 5（已完成）✅
- ✅ log_pipeline M1-M6 全量重写（替换旧 services/parser）
- ✅ 前端日志上传工作流（SessionSidebar + UploadSummaryCard）
- ✅ 安全加固（XSS防护、CORS收窄、安全响应头、依赖漏洞修复）
- ✅ 开发工具链（dev.sh、test-ci.sh、CI 修复、Copilot Skills）
- ✅ 脚本工具链重构（backend/scripts/lib/common.sh）
- ✅ 部署脚本加固（env 模板、systemd 验证、弱密码检测）
- ✅ Embedding 向量检索（`vector_search.py` 真实接入 OpenAI Embedding，`async_search_jira_issues`/`async_search_documents`，持久化索引）(2026-05-03)
- ✅ 向量化批量入库脚本（`scripts/ingest_embeddings.py`）(2026-05-03)
- ✅ `JiraKnowledgeAgent` 接入 embedding 语义检索（`AGENTS_USE_EMBEDDINGS` 开关控制）(2026-05-03)
- ✅ 单元测试全面补全：后端 4 个模块（redaction/chain_log/tool_functions/semantic_cache）+ 前端 7 个 API 路由，后端 145→208 passed，前端 185→201 passed (2026-05-03)

### Sprint 6（进行中）🚧
- ✅ log_pipeline 时间范围修复（catalog.py `ELSE NULL` → `ELSE valid_ts_min/max`；`_file_overlaps()` 处理 `clock_offset=None` 有效时间戳文件）(2026-05-07)
- ✅ EventDigest 上传摘要卡（`EventDigestPanel` 组件，展示最近重启/故障/FOTA结果）(2026-05-07)
- ✅ `time_hint` 可选时间窗口缩窄（`_parse_time_hint()` 解析中文时间描述，orchestrator 注入 context）(2026-05-07)
- ⬜ 真实 Jira 数据同步
- ⬜ 权限体系与操作审计
- ⬜ 生产部署

---

## 📊 进度总览

| 模块 | 完成度 | 状态 |
|------|--------|------|
| 基础设施与部署 | 100% | ✅ 完成 |
| 代码注释与文档 | 100% | ✅ 完成 |
| 离线预处理管线 | 100% | ✅ 完成 |
| 数据库与API | 100% | ✅ 完成 |
| 任务队列集成 | 100% | ✅ 完成 |
| API测试 | 100% | ✅ 完成（后端 344 / 前端 201）|
| MVP核心功能 | 100% | ✅ 完成 |
| 后端核心逻辑（在线诊断增强） | 100% | ✅ 完成 |
| 前端交互功能 | 100% | ✅ 完成 |
| 前端日志上传工作流 | 100% | ✅ 完成（Sprint 5 新增）|
| 数据与演示场景 | 90% | ✅ 基本完成 |
| 评测与验收 | 70% | 🚧 剩余人工评审 |
| 服务增强（缓存/反馈/监控） | 100% | ✅ 完成 |
| log_pipeline（M1-M6）| 100% | ✅ 完成（Sprint 5 新增）|
| 安全加固 | 100% | ✅ 完成（Sprint 5 新增）|
| 开发工具链（CI/dev.sh/Skills）| 100% | ✅ 完成（Sprint 5 新增）|

**总体进度**: 约 **99%**（剩余：真实 Jira 数据同步、权限体系与操作审计、人工评审）

> 测试覆盖率（2026-05-07）：后端 **344 passed**；前端 statements 84.7% / branches 74.4% / functions 83.9% / lines 87.8%，全部高于红线。

---

## 🔗 相关文档

- **[claude.md](../CLAUDE.md)** - 完整项目文档（开发指南、API 文档、部署指南）⭐ 推荐首先阅读
- **[MVP实施总结报告](./MVP实施总结报告.md)** - MVP实施详细报告 ⭐ 最新完成
- [P0任务实施进度报告](./P0任务实施进度报告.md) - P0离线预处理管线实施报告
- [环境安装配置报告](./环境安装配置报告.md) - 环境配置详细报告
- [AI专家项目分析报告](./AI专家项目分析报告.md) - 项目深度分析
- [部署配置完整性检查报告](./部署配置完整性检查报告.md) - 配置完整性检查
- [FOTA智能诊断平台_系统设计方案（v5_废弃）](./FOTA智能诊断平台_系统设计方案（v5_废弃）.md) - 历史设计方案（已废弃）
- [FOTA智能诊断平台_可行性方案（修订版v6）](./FOTA智能诊断平台_可行性方案（修订版v6）.md) - 当前权威设计方案 ⭐
- [FOTA_LLM_API中转方案](./FOTA_LLM_API中转方案.md) - LiteLLM Gateway 架构设计
- [LLM_429限流防御方案](./LLM_429限流防御方案.md) - 限流防御策略
- [Backend README](../backend/README.md) - Backend 部署文档
- [Gateway README](../gateway/README.md) - Gateway 部署文档
- [Web README](../web/README.md) - 前端部署文档

---

**最后更新**: 2026-05-03
**维护人**: AI 开发专家
