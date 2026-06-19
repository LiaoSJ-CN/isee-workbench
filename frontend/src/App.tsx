import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import { Layout, Menu } from 'antd';
import {
  DatabaseOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import {
  DataSourceList,
  ReportList,
  ReportEditor,
  ReportPreview,
  SchedulerPage,
} from './pages';

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

function AppContent() {
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center' }}>
        <div style={{ color: 'white', fontSize: 18, fontWeight: 'bold', marginRight: 32 }}>
          经营分析报表
        </div>
        <AppMenu />
      </Header>
      <Content>
        <Routes>
          <Route path="/" element={<ReportList />} />
          <Route path="/data-sources" element={<DataSourceList />} />
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
      <AppContent />
    </BrowserRouter>
  );
}
