/**
 * 批 13 — Report template marketplace gallery.
 *
 * Card-grid browse surface for the public template pool. Each card
 * shows the template name + description + 数据源 + item count +
 * visibility tag + a "使用此模板" button that forks it into a
 * personal report and routes straight into the editor.
 *
 * Filter bar: category (free-text), data source, visibility (all /
 * public / org / private), and a free-text search over the name.
 * All filters are client-side state — we push them into
 * ``useReportTemplates(filters)`` which bakes them into the cache
 * key, so each filter set gets its own cache slot.
 *
 * "另存为模板" lives in ``ReportEditor`` (owner-or-admin only);
 * this page is just the gallery / fork surface.
 */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Radio,
  Row,
  Select,
  Skeleton,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  AppstoreOutlined,
  CopyOutlined,
  FileTextOutlined,
  SearchOutlined,
} from '@ant-design/icons';

import type { Report, ReportVisibility } from '../types';
import { useDataSources } from '../queries/useDataSources';
import { useReportTemplates, useForkReport } from '../queries/useReportTemplates';
import { formatError } from '../utils/error';

const { Text, Paragraph } = Typography;

const VISIBILITY_OPTIONS: { value: ReportVisibility | 'all'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'public', label: '公开' },
  { value: 'org', label: '同部门' },
  { value: 'private', label: '私有（我的）' },
];

const VISIBILITY_TAG_COLORS: Record<ReportVisibility, string> = {
  public: 'green',
  org: 'gold',
  private: 'blue',
};

const VISIBILITY_LABELS: Record<ReportVisibility, string> = {
  public: '公开',
  org: '同部门',
  private: '私有',
};

export default function ReportTemplates() {
  const navigate = useNavigate();

  // ---- filter state (all client-side; pushed straight into the
  // react-query cache key, no URL sync — list is small enough that
  // a shareable URL filter isn't worth the complexity yet) --------
  const [category, setCategory] = useState<string | undefined>();
  const [dataSourceId, setDataSourceId] = useState<number | undefined>();
  const [visibility, setVisibility] = useState<ReportVisibility | 'all'>('all');
  const [q, setQ] = useState('');

  // Backend strips empty strings — we still pass `q` even when
  // empty so the key stays stable across typing/retyping.
  const filters = useMemo(
    () => ({
      ...(category ? { category } : {}),
      ...(dataSourceId ? { data_source_id: dataSourceId } : {}),
      ...(visibility !== 'all' ? { visibility } : {}),
      q,
    }),
    [category, dataSourceId, visibility, q],
  );

  const { data: dataSources = [] } = useDataSources();
  const { data: templates = [], isPending, isError, error } = useReportTemplates(filters);
  const fork = useForkReport();

  // Distinct categories from the visible fleet (cheap: build on
  // each render — the list is bounded by visibility ACL). Lets
  // users filter to a bucket without us hardcoding a taxonomy.
  const categoryOptions = useMemo(() => {
    const set = new Set<string>();
    templates.forEach((t) => {
      if (t.template_category) set.add(t.template_category);
    });
    return Array.from(set).sort();
  }, [templates]);

  const dsName = (id: number) => dataSources.find((d) => d.id === id)?.name ?? `ID: ${id}`;

  const handleFork = (template: Report) => {
    fork.mutate(
      { templateId: template.id },
      {
        onSuccess: (newReport) => {
          message.success(`已从「${template.name}」创建报表`);
          navigate(`/reports/${newReport.id}`);
        },
        onError: (err) => message.error(formatError(err, '创建失败')),
      },
    );
  };

  // ---- error / loading states ---------------------------------------
  if (isError) {
    return (
      <div style={{ padding: 24 }}>
        <Alert
          type="error"
          showIcon
          message="加载模板失败"
          description={formatError(error, '请稍后重试')}
        />
      </div>
    );
  }

  return (
    <div style={{ padding: 24 }}>
      {/* ---- header -------------------------------------------------*/}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Space align="center" size="middle">
          <AppstoreOutlined style={{ fontSize: 20 }} />
          <h2 style={{ margin: 0 }}>模板市场</h2>
          <Text type="secondary">
            从这里 fork 出一份属于自己的报表，或前往「报表」页把已有报表另存为模板。
          </Text>
        </Space>
      </div>

      {/* ---- filter bar ---------------------------------------------*/}
      <Card size="small" style={{ marginBottom: 16 }} data-testid="templates-filter-bar">
        <Space size="middle" wrap>
          <Input
            placeholder="搜索报表名称"
            prefix={<SearchOutlined />}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            allowClear
            style={{ width: 240 }}
            data-testid="q-input"
          />
          <Select
            placeholder="分类"
            allowClear
            value={category}
            onChange={(v) => setCategory(v)}
            style={{ width: 180 }}
            options={categoryOptions.map((c) => ({ value: c, label: c }))}
            data-testid="category-select"
          />
          <Select
            placeholder="数据源"
            allowClear
            value={dataSourceId}
            onChange={(v) => setDataSourceId(v)}
            style={{ width: 220 }}
            options={dataSources.map((ds) => ({ value: ds.id, label: ds.name }))}
            data-testid="data-source-select"
          />
          <Radio.Group
            value={visibility}
            onChange={(e) => setVisibility(e.target.value as ReportVisibility | 'all')}
            optionType="button"
            buttonStyle="solid"
            data-testid="visibility-radio"
          >
            {VISIBILITY_OPTIONS.map((opt) => (
              <Radio.Button key={opt.value} value={opt.value}>
                {opt.label}
              </Radio.Button>
            ))}
          </Radio.Group>
        </Space>
      </Card>

      {/* ---- gallery ------------------------------------------------*/}
      {isPending ? (
        <Row gutter={[16, 16]}>
          {Array.from({ length: 6 }).map((_, idx) => (
            <Col key={idx} span={8}>
              <Card>
                <Skeleton active paragraph={{ rows: 3 }} />
              </Card>
            </Col>
          ))}
        </Row>
      ) : templates.length === 0 ? (
        <Empty description="暂无可用模板" style={{ padding: 48 }} data-testid="empty-state" />
      ) : (
        <Row gutter={[16, 16]} data-testid="template-grid">
          {templates.map((tpl) => (
            <Col key={tpl.id} span={8}>
              <Card
                hoverable
                data-testid="template-card"
                actions={[
                  <Button
                    key="fork"
                    type="primary"
                    icon={<CopyOutlined />}
                    loading={fork.isPending && fork.variables?.templateId === tpl.id}
                    onClick={() => handleFork(tpl)}
                  >
                    使用此模板
                  </Button>,
                ]}
              >
                <Space direction="vertical" size="small" style={{ width: '100%' }}>
                  <Space size={6} wrap>
                    <FileTextOutlined />
                    <Text strong>{tpl.name}</Text>
                  </Space>
                  <Space size={6} wrap>
                    {tpl.template_category && <Tag color="cyan">{tpl.template_category}</Tag>}
                    <Tag color={VISIBILITY_TAG_COLORS[tpl.visibility ?? 'public']}>
                      {VISIBILITY_LABELS[tpl.visibility ?? 'public']}
                    </Tag>
                  </Space>
                  {tpl.description && (
                    <Paragraph ellipsis={{ rows: 2 }} style={{ marginBottom: 0, color: '#666' }}>
                      {tpl.description}
                    </Paragraph>
                  )}
                  <Space size="middle" wrap>
                    <Text type="secondary">数据源：{dsName(tpl.data_source_id)}</Text>
                    <Text type="secondary">报表项：{tpl.items?.length ?? 0}</Text>
                  </Space>
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </div>
  );
}
