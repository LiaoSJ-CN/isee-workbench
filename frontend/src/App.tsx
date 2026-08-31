import { lazy, Suspense, useState } from 'react';
import {
  BrowserRouter,
  Routes,
  Route,
  Link,
  Navigate,
  useLocation,
  useParams,
} from 'react-router-dom';
import { Layout, Menu, Button } from 'antd';
import {
  AuditOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
  SearchOutlined,
  LogoutOutlined,
  FundOutlined,
  BellOutlined,
  DashboardOutlined,
  AppstoreOutlined,
  MonitorOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { useLogout, useMe } from './queries/useAuth';
import ErrorBoundary from './components/ErrorBoundary';
import { PageSkeleton } from './components/Skeleton';

// 批 10 — every page is loaded lazily so the initial bundle only carries
// the AppShell + shared vendor chunks (react/antd/router/rq). The page
// chunks (and route-local vendors like @dnd-kit, @codemirror) are
// fetched on first navigation. Login stays eager because (a) it's the
// entry point of the unauthenticated flow and (b) wrapping it in a
// Suspense that resolves to a spinner before login is silly.
import Login from './pages/Login';

const DataSourceList = lazy(() => import('./pages/DataSourceList'));
const ReportList = lazy(() => import('./pages/ReportList'));
// 批 13 — template marketplace gallery. Loaded lazily alongside the
// regular report list so the initial bundle stays unaffected.
const ReportTemplates = lazy(() => import('./pages/ReportTemplates'));
const ReportEditor = lazy(() => import('./pages/ReportEditor'));
const ReportPreview = lazy(() => import('./pages/ReportPreview'));
const SchedulerPage = lazy(() => import('./pages/Scheduler'));
const DataExplorer = lazy(() => import('./pages/DataExplorer'));
const AuditLogPage = lazy(() => import('./pages/AuditLogPage'));
// 批 12 — admin-only pool metrics dashboard. Gated by ``RequireAdmin``
const AdminMetrics = lazy(() => import('./pages/AdminMetrics'));
const MySubscriptionsPage = lazy(() => import('./pages/MySubscriptions'));
const ReportHistoryPage = lazy(() => import('./pages/ReportHistory'));
const ReportHistoryDiffPage = lazy(() => import('./pages/ReportHistory/DiffView'));
// 批 14.3 — dashboard pages. Lazy-loaded like the rest of the report
// surfaces so the initial bundle still ships just the AppShell.
const DashboardList = lazy(() => import('./pages/DashboardList'));
const DashboardView = lazy(() => import('./pages/DashboardView'));
const DashboardEdit = lazy(() => import('./pages/DashboardEdit'));
// 批 user-management S3+S4 — admin user-management page. Gated by
// ``RequireAdmin`` (App.tsx:164) like the other admin surfaces.
const AdminUsers = lazy(() => import('./pages/admin/Users'));

const { Header, Content } = Layout;

function AppMenu() {
  const location = useLocation();
  // Pull role to gate the audit-log menu item (批 9.6). The hook is
  // already pre-warmed by `RequireAuth` so this is a cached read.
  const me = useMe();
  const isAdmin = me.data?.role === 'admin';

  const items = [
    {
      key: '/data-sources',
      icon: <DatabaseOutlined />,
      label: <Link to="/data-sources">数据源</Link>,
    },
    {
      key: '/explorer',
      icon: <SearchOutlined />,
      label: <Link to="/explorer">数据探索</Link>,
    },
    {
      key: '/reports',
      icon: <FileTextOutlined />,
      label: <Link to="/reports">报表</Link>,
    },
    // 批 13 — template marketplace. Sits next to /reports so the
    // gallery feels like a sibling surface (not a sub-page of
    // /reports) — operators browse templates, then fork into a
    // personal report, which lands them back on /reports/{id}.
    {
      key: '/reports/templates',
      icon: <AppstoreOutlined />,
      label: <Link to="/reports/templates">模板市场</Link>,
    },
    {
      key: '/scheduler',
      icon: <ClockCircleOutlined />,
      label: <Link to="/scheduler">定时任务</Link>,
    },
    // 批 14.3 — dashboard surfaces. Sits next to /scheduler so
    // operators see dashboards as a top-level workflow (compose +
    // schedule + view), not buried under /reports.
    {
      key: '/dashboards',
      icon: <DashboardOutlined />,
      label: <Link to="/dashboards">看板</Link>,
    },
    {
      key: '/my-subscriptions',
      icon: <BellOutlined />,
      label: <Link to="/my-subscriptions">我的订阅</Link>,
    },
    ...(isAdmin
      ? [
          {
            key: '/admin/users',
            icon: <TeamOutlined />,
            label: <Link to="/admin/users">用户管理</Link>,
          },
          {
            key: '/audit-logs',
            icon: <AuditOutlined />,
            label: <Link to="/audit-logs">审计日志</Link>,
          },
          {
            key: '/admin/metrics',
            icon: <MonitorOutlined />,
            label: <Link to="/admin/metrics">监控仪表盘</Link>,
          },
        ]
      : []),
  ];

  return (
    <Menu
      theme="dark"
      mode="horizontal"
      selectedKeys={[location.pathname]}
      items={items}
      style={{ flex: 1 }}
    />
  );
}

/** Gate: verifies session on mount, redirects to /login if 401. */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const me = useMe();

  if (me.isPending) {
    return <PageSkeleton />;
  }
  if (me.isError) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <>{children}</>;
}

/**
 * Gate: admin-only route (批 9.6).
 *
 * Defence in depth — the backend ``GET /audit-logs`` is itself gated
 * by ``admin_required``, so a non-admin who somehow ended up here
 * would get a 403 on the very first request. The route guard keeps
 * non-admins from seeing admin-only menu items and from noticing
 * pages they shouldn't reach; the backend gate stops them from
 * reading the data. The two layers share no state — if either is
 * broken, the other still holds.
 *
 * Must be rendered inside ``RequireAuth`` so ``/me`` is cached.
 */
function RequireAdmin({ children }: { children: React.ReactNode }) {
  const me = useMe();
  if (me.isPending) {
    return <PageSkeleton />;
  }
  if (!me.data || me.data.role !== 'admin') {
    // Non-admin (and unlikely "no user at all") — bounce back to the
    // default landing page. We deliberately do not toast here: a URL
    // typo shouldn't produce a scolding message.
    return <Navigate to="/reports" replace />;
  }
  return <>{children}</>;
}

// 批 11.4: redirect stale `/reports/:id/edit` links (left over from
// the earlier clone-success callback) to the canonical edit URL.
function NavigateToReports() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/reports/${id ?? ''}`} replace />;
}

function AppShell() {
  const logout = useLogout();
  const [, setNav] = useState(0); // trigger re-render after logout redirect

  const handleLogout = () => {
    logout.mutate(undefined, {
      onSuccess: () => {
        window.location.href = '/login';
      },
      onError: () => {
        // Even on logout failure, bounce to login to clear local state.
        window.location.href = '/login';
        setNav((n) => n + 1);
      },
    });
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center' }}>
        <div
          style={{
            color: 'white',
            fontSize: 18,
            fontWeight: 'bold',
            marginRight: 32,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <FundOutlined />
          iSee数据分析工作台
        </div>
        <AppMenu />
        <Button
          type="text"
          icon={<LogoutOutlined />}
          onClick={handleLogout}
          style={{ color: 'white' }}
        >
          退出
        </Button>
      </Header>
      <Content>
        <Suspense fallback={<PageSkeleton />}>
          <Routes>
            <Route path="/" element={<ReportList />} />
            <Route path="/data-sources" element={<DataSourceList />} />
            <Route path="/explorer" element={<DataExplorer />} />
            <Route path="/reports" element={<ReportList />} />
            {/* 批 13 — template gallery. Declared BEFORE /reports/:id
                so the literal path doesn't get parsed as a numeric
                id (React Router matches in declaration order, and
                /:id would greedily eat the "templates" segment). */}
            <Route path="/reports/templates" element={<ReportTemplates />} />
            <Route path="/reports/:id" element={<ReportEditor />} />
            <Route path="/reports/:id/edit" element={<NavigateToReports />} />
            <Route path="/reports/:id/preview" element={<ReportPreview />} />
            <Route path="/reports/:id/history" element={<ReportHistoryPage />} />
            <Route path="/reports/:id/history/:vid" element={<ReportHistoryDiffPage />} />
            <Route path="/scheduler" element={<SchedulerPage />} />
            <Route path="/my-subscriptions" element={<MySubscriptionsPage />} />
            {/* 批 14.3 — dashboard routes. /:id/edit declared AFTER
                /:id so React Router matches the more specific path
                first; the same trick used by /reports/:id vs
                /reports/templates above. */}
            <Route path="/dashboards" element={<DashboardList />} />
            <Route path="/dashboards/:id" element={<DashboardView />} />
            <Route path="/dashboards/:id/edit" element={<DashboardEdit />} />
            <Route
              path="/audit-logs"
              element={
                <RequireAdmin>
                  <AuditLogPage />
                </RequireAdmin>
              }
            />
            <Route
              path="/admin/metrics"
              element={
                <RequireAdmin>
                  <AdminMetrics />
                </RequireAdmin>
              }
            />
            <Route
              path="/admin/users"
              element={
                <RequireAdmin>
                  <AdminUsers />
                </RequireAdmin>
              }
            />
          </Routes>
        </Suspense>
      </Content>
    </Layout>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/*"
          element={
            <RequireAuth>
              <ErrorBoundary>
                <AppShell />
              </ErrorBoundary>
            </RequireAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
