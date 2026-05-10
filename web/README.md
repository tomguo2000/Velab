# FOTA 智能诊断平台 - Web 前端

基于 Next.js 16 构建的 FOTA 诊断平台前端应用，提供实时流式诊断交互界面。

---

## 📋 目录结构

```
web/
├── src/
│   ├── app/                    # Next.js App Router
│   │   ├── api/
│   │   │   ├── chat/           # SSE 诊断流代理
│   │   │   ├── upload-log/     # 日志包上传代理
│   │   │   ├── bundle-status/  # Bundle 处理状态轮询
│   │   │   ├── bundle-events/  # Bundle 事件查询
│   │   │   ├── bundle-logs/    # Bundle 日志内容查询
│   │   │   ├── sessions/       # 会话 CRUD
│   │   │   ├── parse-status/   # 解析任务状态
│   │   │   └── session-title/  # 会话标题生成
│   │   ├── page.tsx           # 主页面
│   │   ├── layout.tsx         # 根布局
│   │   └── globals.css        # 全局样式
│   ├── components/            # React 组件
│   │   ├── ChatMessage.tsx    # 消息组件（含 XSS 防护）
│   │   ├── ThinkingProcess.tsx # Agent 执行状态展示
│   │   ├── InputBar.tsx       # 输入框
│   │   ├── Header.tsx         # 页头（场景切换）
│   │   ├── WelcomePage.tsx    # 欢迎页
│   │   ├── FeedbackButtons.tsx # 反馈按钮
│   │   ├── SourcePanel.tsx    # 引用来源面板
│   │   ├── SessionSidebar.tsx # 历史会话侧边栏
│   │   └── UploadSummaryCard.tsx # 日志上传摘要卡
│   └── lib/                   # 工具库
│       ├── types.ts           # TypeScript 类型定义
│       ├── sseParse.ts        # SSE 流解析器
│       └── bundleStatus.ts    # Bundle 状态轮询客户端
├── public/                    # 静态资源
├── package.json               # 依赖配置
├── tsconfig.json              # TypeScript 配置
├── next.config.ts             # Next.js 配置（含安全响应头、outputFileTracingRoot）
└── postcss.config.mjs          # PostCSS / Tailwind CSS v4 配置
```

---

## 🚀 快速启动（开发环境）

### 1. 安装依赖

```bash
npm install
# 或
yarn install
# 或
pnpm install
```

### 2. 配置环境变量

```bash
cp .env.example .env.local
# 编辑 .env.local 文件，配置后端服务地址
```

`.env.local` 示例：
```bash
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
BACKEND_URL=http://localhost:8000
```

### 3. 启动开发服务器

```bash
npm run dev
# 或
yarn dev
# 或
pnpm dev
```

访问 [http://localhost:3000](http://localhost:3000) 查看应用。

### 4. 构建生产版本

```bash
npm run build
npm run start
```

---

## 🏭 生产环境部署

### Vercel 部署（推荐）

1. 将代码推送到 GitHub
2. 在 [Vercel](https://vercel.com) 导入项目
3. 配置环境变量：
   - `NEXT_PUBLIC_BACKEND_URL`: 后端 API 地址（公开）
   - `BACKEND_URL`: 后端 API 地址（服务端）
4. 部署

### Docker 部署

```bash
# 构建镜像
docker build -t fota-web .

# 运行容器
docker run -d -p 3000:3000 \
  -e NEXT_PUBLIC_BACKEND_URL=https://api.example.com \
  -e BACKEND_URL=https://api.example.com \
  fota-web
```

### 传统服务器部署

```bash
# 构建
npm run build

# 使用 PM2 运行
pm2 start npm --name "fota-web" -- start

# 或使用 systemd
sudo cp systemd/fota-web.service /etc/systemd/system/
sudo systemctl enable fota-web
sudo systemctl start fota-web
```

---

## 🎨 核心功能

### 1. 实时流式诊断

- 基于 SSE (Server-Sent Events) 的实时数据流
- 逐步展示 Agent 执行过程（Thinking Process）
- **工作区排查进度展示**：实时渲染 `todo.md` 和 `notes.md` 的增量更新（Checklist/摘要）
- 流式输出最终诊断结果

### 2. 多场景支持

- FOTA 诊断
- Jira 工单检索
- 车队分析
- CES 演示
- 数据采集

### 3. 交互体验

- Markdown 格式渲染（标题、列表、代码块、表格）
- 流式输出光标动画
- 可折叠的思考过程
- 反馈按钮（点赞/点踩）
- 自动滚动到最新消息

### 4. 日志上传工作流（Sprint 5 新增）

- 拖拽或选择日志压缩包上传（zip/tar.gz）
- 实时展示解析进度（轮询 bundle 状态）
- 上传完成后展示摘要（文件数、事件数、控制器分类）
- 错误状态友好展示

### 5. 会话持久化

- 侧边栏展示历史会话列表
- 支持新建 / 切换 / 删除会话
- 刷新页面后自动恢复上次会话

### 6. 响应式设计

- 支持桌面和移动设备
- 深色主题（基于 CSS 变量）
- 流畅的动画效果

### 7. 安全加固（Sprint 5 新增）

- **XSS 防护**：`ChatMessage.tsx` 内置 `escapeHtml()` / `sanitizeUrl()` 过滤
- **安全响应头**：`X-Content-Type-Options` / `X-Frame-Options` / `X-XSS-Protection` / `Referrer-Policy` / `Permissions-Policy`
- **输入验证**：`/api/chat` 请求体长度和格式限制
- **依赖漏洞**：`npm audit` 输出 0 vulnerabilities

---

## 🔧 开发指南

### 技术栈

- **框架**: Next.js 16 (App Router)
- **语言**: TypeScript
- **样式**: Tailwind CSS + CSS Variables
- **状态管理**: React Hooks
- **实时通信**: SSE (Server-Sent Events)

### 项目特点

1. **轻量级 Markdown 渲染器**
   - 无需外部库，自实现常见 Markdown 语法
   - 支持标题、列表、代码块、表格等

2. **SSE 流式处理**
   - 自定义 SSE 解析器
   - 支持增量更新和状态管理

3. **主题系统**
   - 基于 CSS 变量的主题切换
   - 易于扩展和定制

### 添加新组件

```typescript
// src/components/MyComponent.tsx
"use client";

import { useState } from "react";

export default function MyComponent() {
  const [state, setState] = useState("");
  
  return (
    <div className="p-4">
      {/* 组件内容 */}
    </div>
  );
}
```

### 修改主题

编辑 `src/app/globals.css` 中的 CSS 变量：

```css
:root {
  --bg-primary: #0a0a0a;
  --text-primary: #e5e5e5;
  --accent-red: #ef4444;
  /* ... */
}
```

---

## 📊 性能优化

### 已实施的优化

1. **代码分割**
   - 按路由自动分割
   - 组件懒加载

2. **图片优化**
   - Next.js Image 组件
   - 自动格式转换和压缩

3. **字体优化**
   - next/font 自动优化
   - 字体子集化

### 建议的优化

1. **虚拟滚动**
   - 对长对话历史使用虚拟列表
   - 减少 DOM 节点数量

2. **缓存策略**
   - 使用 SWR 或 React Query
   - 缓存常见问题的响应

3. **SSE 缓冲优化**
   - 批量处理 SSE 事件
   - 减少状态更新频率

---

## 🧪 测试

本项目使用 **Vitest 4.1.2** 作为测试框架，配合 React Testing Library 和 MSW 进行组件测试和 API 模拟。

### 测试框架

- **测试框架**: Vitest 4.1.2
- **组件测试**: @testing-library/react 16.3.2
- **API 模拟**: MSW (Mock Service Worker) 2.12.14
- **覆盖率工具**: @vitest/coverage-v8
- **可视化界面**: @vitest/ui

### 测试命令

```bash
# 运行所有测试
npm test

# 监听模式（开发时使用）
npm run test:watch

# 生成覆盖率报告
npm run test:coverage

# CI 覆盖率门禁（branches/functions/lines/statements）
npm run test:ci

# 可视化测试界面
npm run test:ui
```

### 配置文件

- [`vitest.config.ts`](vitest.config.ts:1) - Vitest 配置文件。全局覆盖率阈值为 branches ≥ 70%、functions ≥ 70%、lines ≥ 80%、statements ≥ 80%；`src/__tests__/**` 为测试辅助代码，不计入生产覆盖率门禁。
- [`vitest.setup.ts`](vitest.setup.ts:1) - Vitest 设置文件

### 详细测试文档

完整的测试指南、最佳实践和覆盖率要求请参考：
- **[`README_TESTING.md`](README_TESTING.md:1)** - 完整测试文档 ⭐

---

## 🔗 相关文档

- [Next.js 文档](https://nextjs.org/docs)
- [Tailwind CSS 文档](https://tailwindcss.com/docs)
- [TypeScript 文档](https://www.typescriptlang.org/docs)
- [项目完整文档](../claude.md)
- [后端 API 文档](../backend/README.md)

---

## 📝 核心特性

- ✅ **实时流式响应**: 基于 SSE 的实时诊断过程展示
- ✅ **多场景支持**: 支持多种诊断场景切换
- ✅ **Markdown 渲染**: 轻量级 Markdown 解析器
- ✅ **响应式设计**: 支持桌面和移动设备
- ✅ **主题系统**: 基于 CSS 变量的深色主题
- ✅ **TypeScript**: 完整的类型安全

---

## 🚨 故障排查

### 问题 1：无法连接后端

```bash
# 检查环境变量
cat .env.local

# 检查后端服务是否运行
curl http://localhost:8000/health

# 检查 CORS 配置
# 确保后端允许前端域名访问
```

### 问题 2：SSE 连接中断

```bash
# 检查网络连接
# 检查后端日志
# 确认超时配置（默认 120 秒）
```

### 问题 3：样式不生效

```bash
# 清除 Next.js 缓存
rm -rf .next

# 重新构建
npm run build
```

---

## 📞 技术支持

如有问题，请查看：
1. 本 README 的故障排查章节
2. [项目完整文档](../claude.md)
3. Next.js 官方文档

---

**项目状态**: 🚧 开发中  
**最后更新**: 2026-05-03  
**维护团队**: FOTA 诊断平台团队
