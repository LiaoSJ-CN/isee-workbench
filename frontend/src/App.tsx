import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Link, Navigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, Spin } from 'antd';
import {
  DatabaseOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
  SearchOutlined,
  LogoutOutlined,
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
import { authApi } from './api';

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
  const [checking, setChecking] = useState(true);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    authApi
      .me()
      .then(() => {
        if (!cancelled) setAuthed(true);
      })
      .catch(() => {
        // 401 already triggered a global redirect to /login; nothing to do.
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (checking) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }
  if (!authed) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <>{children}</>;
}

function AppShell() {
  const handleLogout = async () => {
    try {
      await authApi.logout();
    } finally {
      window.location.href = '/login';
    }
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center' }}>
        <div style={{ color: 'white', fontSize: 18, fontWeight: 'bold', marginRight: 32 }}>
          经营分析报表
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
              <AppShell />
            </RequireAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
