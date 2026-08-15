import { Button, DatePicker, Form, Input, InputNumber, Select, Space, Switch } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';

import type { ReportParameter } from '../types';

interface ReportParameterFormProps {
  parameters: ReportParameter[];
  /** Called with `{ [param.name]: value }` on submit. Values are
   *  type-coerced (string/number/ISO-date-string/enum-string/bool). */
  onSubmit: (values: Record<string, unknown>) => void;
  /** Submit-button + DatePicker spinner state, owned by the parent. */
  loading?: boolean;
  /** Hide the submit button when the parent renders its own (e.g. inside
   *  a Modal). Default: render the button. */
  hideSubmit?: boolean;
  /** Override the submit-button label. */
  submitLabel?: string;
}

/**
 * `ReportParameterForm` — renders one Antd `Form.Item` per parameter,
 * type-switched on `parameter.type`. The `name` (not `label`) is used
 * as the form-field key so `onSubmit` can hand values straight to the
 * job queue without remapping.
 *
 * Defaults are pre-populated from each parameter's `default` field;
 * `required` and `enum` membership are enforced via Antd `rules`. Date
 * values are normalised to ISO-8601 (`YYYY-MM-DD`) to match the
 * backend's `datetime.fromisoformat` validator.
 */
export function ReportParameterForm({
  parameters,
  onSubmit,
  loading = false,
  hideSubmit = false,
  submitLabel = '提交',
}: ReportParameterFormProps) {
  if (parameters.length === 0) {
    return (
      <div style={{ color: '#999', padding: '12px 0' }}>该报表未声明参数，可直接导出。</div>
    );
  }

  // Pre-fill from defaults — keyed by `parameter.name` because the form
  // field uses `name` as the key too. Date defaults come in as ISO
  // strings and need to round-trip through Dayjs so the DatePicker
  // recognises them.
  const initialValues: Record<string, unknown> = {};
  for (const p of parameters) {
    if (p.default === null || p.default === undefined) continue;
    if (p.type === 'date') {
      initialValues[p.name] = dayjs(p.default as string);
    } else {
      initialValues[p.name] = p.default;
    }
  }

  const handleFinish = (values: Record<string, unknown>) => {
    const coerced: Record<string, unknown> = {};
    for (const p of parameters) {
      const raw = values[p.name];
      if (raw === undefined || raw === null) {
        // Honour `required` enforcement — Antd already rejected empty
        // submits, so a missing value here means the field is optional
        // and the user left it blank. Skip rather than send `null`.
        continue;
      }
      if (p.type === 'date') {
        const d = raw as Dayjs;
        coerced[p.name] = d.format('YYYY-MM-DD');
      } else if (p.type === 'number') {
        coerced[p.name] = Number(raw);
      } else {
        coerced[p.name] = raw;
      }
    }
    onSubmit(coerced);
  };

  return (
    <Form layout="vertical" initialValues={initialValues} onFinish={handleFinish}>
      {parameters.map((p) => {
        const rules = [{ required: p.required, message: `请填写 ${p.label}` }];
        return (
          <Form.Item
            key={p.id}
            name={p.name}
            label={p.label}
            rules={rules}
            valuePropName={p.type === 'bool' ? 'checked' : 'value'}
          >
            {renderInput(p)}
          </Form.Item>
        );
      })}
      {!hideSubmit && (
        <Form.Item style={{ marginBottom: 0 }}>
          <Space>
            <Button type="primary" htmlType="submit" loading={loading}>
              {submitLabel}
            </Button>
          </Space>
        </Form.Item>
      )}
    </Form>
  );
}

function renderInput(p: ReportParameter) {
  switch (p.type) {
    case 'string':
      return <Input placeholder={`请输入${p.label}`} allowClear />;
    case 'number':
      return <InputNumber placeholder={`请输入${p.label}`} style={{ width: '100%' }} />;
    case 'date':
      return <DatePicker style={{ width: '100%' }} />;
    case 'enum':
      return (
        <Select
          placeholder={`请选择${p.label}`}
          options={(p.options ?? []).map((opt) => ({ value: opt, label: opt }))}
          allowClear
        />
      );
    case 'bool':
      return <Switch />;
  }
}