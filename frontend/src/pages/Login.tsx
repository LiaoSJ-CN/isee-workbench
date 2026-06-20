import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Card, Form, Input, Button, Typography, message } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import axios from 'axios';
import { authApi } from '../api';

const { Title } = Typography;

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const [submitting, setSubmitting] = useState(false);

  // Where to go after a successful login. The router state can carry a
  // `from` location so deep links survive a login redirect.
  const from = (location.state as { from?: string } | null)?.from ?? '/';

  const onFinish = async (values: { username: string; password: string }) => {
    setSubmitting(true);
    try {
      await authApi.login(values.username, values.password);
      message.success('登录成功');
      navigate(from, { replace: true });
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        message.error('用户名或密码错误');
      } else if (axios.isAxiosError(err)) {
        // CORS preflight failure, network error, 5xx, etc — surface details.
        message.error(
          `登录失败 (${err.response?.status ?? 'network'}): ${err.message}`
        );
        console.error('login error', err);
      } else {
        message.error('登录失败: ' + String(err));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f0f2f5',
      }}
    >
      <Card style={{ width: 360, boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <Title level={3} style={{ margin: 0 }}>
            经营分析报表
          </Title>
          <Typography.Text type="secondary">请登录</Typography.Text>
        </div>
        <Form layout="vertical" onFinish={onFinish} autoComplete="off">
          <Form.Item
            label="用户名"
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input prefix={<UserOutlined />} placeholder="admin" autoFocus />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="admin" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" loading={submitting} block>
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}