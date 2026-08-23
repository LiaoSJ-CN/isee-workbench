import { useState } from 'react';
import { BrowserRouter, Routes, Route, Link, Navigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, Spin } from 'antd';
import {
  AuditOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
  SearchOutlined,
  LogoutOutlined,
  FundOutlined,
} from '@ant-design/icons';
import {
  AuditLogPage,
  DataSourceList,
  ReportList,
  ReportEditor,
  ReportPreview,
  SchedulerPage,
  DataExplorer,
  Login,
} from './pages';
import { useLogout, useMe } from './queries/useAuth';
import ErrorBoundary from './components/ErrorBoundary';

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
    {
      key: '/scheduler',
      icon: <ClockCircleOutlined />,
      label: <Link to="/scheduler">定时任务</Link>,
    },
    ...(isAdmin
      ? [
          {
            key: '/audit-logs',
            icon: <AuditOutlined />,
            label: <Link to="/audit-logs">审计日志</Link>,
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
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    );
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
    return (
      <div style={{ minHeight: '60vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }
  if (!me.data || me.data.role !== 'admin') {
    // Non-admin (and unlikely "no user at all") — bounce back to the
    // default landing page. We deliberately do not toast here: a URL
    // typo shouldn't produce a scolding message.
    return <Navigate to="/reports" replace />;
  }
  return <>{children}</>;
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
        <div style={{ color: 'white', fontSize: 18, fontWeight: 'bold', marginRight: 32, display: 'flex', alignItems: 'center', gap: 8 }}>
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
        <Routes>
          <Route path="/" element={<ReportList />} />
          <Route path="/data-sources" element={<DataSourceList />} />
          <Route path="/explorer" element={<DataExplorer />} />
          <Route path="/reports" element={<ReportList />} />
          <Route path="/reports/:id" element={<ReportEditor />} />
          <Route path="/reports/:id/preview" element={<ReportPreview />} />
          <Route path="/scheduler" element={<SchedulerPage />} />
          <Route
            path="/audit-logs"
            element={
              <RequireAdmin>
                <AuditLogPage />
              </RequireAdmin>
            }
          />
        </Routes>
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
