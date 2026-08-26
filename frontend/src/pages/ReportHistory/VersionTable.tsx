import { Button, Space, Table, Tag, Tooltip } from 'antd';
import { useNavigate } from 'react-router-dom';
import type { Report, ReportVersionSummary } from '../../types';
import { isOwnerOrAdmin, useCurrentUser } from '../../queries/useCurrentUser';

interface Props {
  reportId: number;
  report: Pick<Report, 'owner_user_id'>;
  versions: ReportVersionSummary[];
  onRestore: (v: ReportVersionSummary) => void;
  onDelete: (v: ReportVersionSummary) => void;
}

export function VersionTable({ reportId, report, versions, onRestore, onDelete }: Props) {
  const user = useCurrentUser();
  const navigate = useNavigate();
  // Mirrors the server's owner-or-admin gate on POST/DELETE /versions
  // so non-owner editors don't see a button that would 403 on click.
  const canMutate = isOwnerOrAdmin(user, report);

  return (
    <Table<ReportVersionSummary>
      rowKey="id"
      dataSource={versions}
      pagination={false}
      columns={[
        {
          title: '版本',
          dataIndex: 'version_number',
          render: (n, row) => (
            <Space>
              <strong>v{n}</strong>
              {row.is_pinned && <Tag color="gold">已固定</Tag>}
            </Space>
          ),
        },
        { title: '标签', dataIndex: 'label', render: (l) => l || '—' },
        { title: '创建人', dataIndex: 'created_by' },
        {
          title: '创建时间',
          dataIndex: 'created_at',
          render: (s: string) => new Date(s).toLocaleString(),
        },
        {
          title: '操作',
          render: (_, row) => (
            <Space>
              <Button
                size="small"
                onClick={() => navigate(`/reports/${reportId}/history/${row.id}`)}
              >
                查看
              </Button>
              <Button size="small" disabled={!canMutate} onClick={() => onRestore(row)}>
                恢复
              </Button>
              <Tooltip title={row.is_pinned ? '已固定的版本不可删除' : ''}>
                <Button
                  size="small"
                  danger
                  disabled={!canMutate || row.is_pinned}
                  onClick={() => onDelete(row)}
                >
                  删除
                </Button>
              </Tooltip>
            </Space>
          ),
        },
      ]}
    />
  );
}
