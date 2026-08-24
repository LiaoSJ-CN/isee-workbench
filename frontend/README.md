# iSee 工作台 — 前端

React 19 + TypeScript + Vite + Ant Design 的 SPA。配套 [README.md](../README.md) / [DEPLOY.md](../DEPLOY.md) / [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) 一起读。

## 技术栈

| 用途 | 库 |
|------|----|
| 框架 | React 19 + TypeScript |
| 构建 | Vite 8 |
| UI | Ant Design 6（`antd`） |
| 拖拽 | `@dnd-kit/core` + `@dnd-kit/sortable` |
| SQL 编辑器 | CodeMirror 6（`@codemirror/lang-sql`） |
| 数据获取 | TanStack React Query 5 + axios |
| 路由 | React Router 7 |
| 日期 | dayjs |
| 测试 | vitest + @testing-library/react + happy-dom |
| E2E | Playwright |
| Lint | ESLint 10（typescript-eslint + react-hooks + react-refresh + jsx-a11y） |
| Format | Prettier 3 |

## 常用命令

```bash
npm install               # 装依赖
npm run dev               # 启动 dev server (:5173)
npm run build             # 类型检查 + 生产构建
npm run preview           # 预览生产构建
npm run lint              # ESLint
npm run format            # Prettier --write（CI 会跑 --check 拦回归）
npm run format:check      # Prettier --check（CI 步骤）
npm test                  # vitest run
npm run test:watch        # vitest watch
npm run test:coverage     # vitest + v8 coverage
npm run test:e2e          # playwright
```

## 目录

```
src/
├── api/                  # axios 客户端 + 后端接口封装
├── components/           # 公共组件
│   ├── SqlEditor.tsx                  # CodeMirror 6 SQL 编辑器（编辑器 / 详情页都用）
│   ├── SchemaTree.tsx                 # DataExplorer 左侧 schema 树
│   ├── ReportParameterForm.tsx        # 报表参数表单
│   ├── ReportShareModal.tsx           # 报表 share / visibility 编辑
│   ├── DataSourceShareModal.tsx       # 数据源 share 编辑
│   ├── SubscriptionModal.tsx          # 订阅创建 / 编辑弹窗
│   ├── Skeleton.tsx                   # 加载占位
│   └── ErrorBoundary.tsx              # 顶层错误边界
├── pages/                # 页面级组件（路由目标）
│   ├── Login.tsx
│   ├── DataSourceList.tsx
│   ├── DataExplorer.tsx
│   ├── ReportList.tsx
│   ├── ReportEditor/     # 拖拽式报表构建器（多文件）
│   ├── ReportPreview.tsx
│   ├── Scheduler.tsx
│   ├── MySubscriptions.tsx
│   └── AuditLogPage.tsx  # admin only
├── queries/              # React Query hooks（按域分文件）
├── constants/            # DataExplorer 模板分类 + 其他常量
├── types/                # 与后端 Pydantic schema 对应的 TS 类型
├── utils/                # 通用工具（cn、date、error-handler 等）
├── App.tsx               # 顶层布局 + 路由
└── main.tsx              # 入口
```

## API 代理

开发环境：`vite.config.ts` 的 `server.proxy` 把 `/api` 转发到 `http://localhost:8000`，自动 `rewrite` 去除前缀。

Docker 生产环境：`frontend/nginx.conf` 的 `location /api/` 通过 `proxy_pass http://backend:8000/`（尾部斜杠剥离前缀）。

构建时可通过 `VITE_API_BASE_URL` 覆盖默认 `/api`。

## 鉴权流

- 登录拿 token，存 Cookie（HttpOnly + SameSite，浏览器自动带）+ axios header fallback
- axios interceptor：请求时优先 Cookie，无 Cookie 时回落到 `Authorization: Bearer`
- 401 响应：尝试 refresh，refresh 失败才跳登录页

## 代码风格

- Prettier 3 配置见 `.prettierrc`（无单引号 / 分号等看文件本身）
- ESLint 用 `typescript-eslint` 推荐规则 + React Hooks / Refresh / a11y 插件
- 组件用 `function ComponentName()` 形式（不写 `React.FC`），文件名 PascalCase
- 状态管理：组件内 `useState` 跨组件用 React Query（不引 Redux / Zustand）

## 测试要点

- vitest 默认 `happy-dom` 环境
- 单元测试用 `@testing-library/react` + `userEvent`
- 报表编辑器 / DataExplorer 的复杂交互单独建组件测
- e2e 用 Playwright，落地关键用户旅程（登录 → 建数据源 → 建报表 → 预览 → 导出）