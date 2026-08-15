import { useState } from 'react';
import { BrowserRouter, Routes, Route, Link, Navigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, Spin } from 'antd';
import {
  DatabaseOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
  SearchOutlined,
  LogoutOutlined,
  FundOutlined,
} from '@ant-design/icons';
import {
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
