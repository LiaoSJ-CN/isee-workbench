import { useEffect, useState } from 'react';
import { Modal, Form, Input, Select, InputNumber, DatePicker, Switch } from 'antd';
import dayjs from 'dayjs';
import type {
  ReportParameter,
  ParameterType,
  ReportParameterCreate,
  ReportParameterUpdate,
} from '../../types';

export interface ParameterEditorModalProps {
  visible: boolean;
  parameter: ReportParameter | null;
  onSave: (payload: ReportParameterCreate | ReportParameterUpdate) => void;
  onCancel: () => void;
  saving?: boolean;
}

export function ParameterEditorModal({
  visible,
  parameter,
  onSave,
  onCancel,
  saving,
}: ParameterEditorModalProps) {
  const [form] = Form.useForm();
  const [paramType, setParamType] = useState<ParameterType>('string');

  // Sync the form with the current `parameter` whenever the modal opens.
  // `destroyOnClose` would re-mount the Form, but using `useEffect` on
  // `visible` keeps the input field state stable when the user toggles
  // between create/edit without closing.
  useEffect(() => {
    if (!visible) return;
    if (parameter) {
      form.setFieldsValue({
        name: parameter.name,
        label: parameter.label,
        type: parameter.type,
        required: parameter.required,
        options: parameter.options ?? [],
        default:
          parameter.type === 'date' && parameter.default
            ? dayjs(parameter.default as string)
            : (parameter.default ?? null),
      });
      setParamType(parameter.type);
    } else {
      form.resetFields();
      form.setFieldsValue({ type: 'string', required: true });
      setParamType('string');
    }
  }, [visible, parameter, form]);

  const handleTypeChange = (t: ParameterType) => {
    setParamType(t);
    // Wipe `default` because its runtime type changes with the variant.
    form.setFieldValue('default', null);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      const payload: Record<string, unknown> = {
        name: values.name,
        label: values.label,
        type: values.type,
        required: values.required,
      };
      const rawDefault = values.default;
      if (rawDefault !== undefined && rawDefault !== null && rawDefault !== '') {
        if (values.type === 'date') {
          payload.default = (rawDefault as dayjs.Dayjs).format('YYYY-MM-DD');
        } else if (values.type === 'number') {
          payload.default = Number(rawDefault);
        } else {
          payload.default = rawDefault;
        }
      } else {
        payload.default = null;
      }
      if (values.type === 'enum') {
        payload.options = (values.options ?? []) as string[];
      }
      onSave(payload as unknown as ReportParameterCreate);
    } catch {
      // Antd already surfaces the field-level error message; no global toast needed.
    }
  };

  return (
    <Modal
      title={parameter ? '编辑参数' : '添加参数'}
      open={visible}
      onOk={handleSubmit}
      onCancel={onCancel}
      confirmLoading={saving}
      width={520}
      destroyOnHidden
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="name"
          label="名称 (标识符)"
          rules={[
            { required: true, message: '请输入参数名' },
            {
              pattern: /^[A-Za-z_][A-Za-z0-9_]*$/,
              message: '只能以字母/下划线开头，后跟字母/数字/下划线',
            },
          ]}
        >
          <Input placeholder="例如: region" disabled={!!parameter} />
        </Form.Item>
        <Form.Item name="label" label="标签" rules={[{ required: true, message: '请输入标签' }]}>
          <Input placeholder="例如: 区域" />
        </Form.Item>
        <Form.Item name="type" label="类型" rules={[{ required: true }]}>
          <Select
            options={[
              { value: 'string', label: '字符串' },
              { value: 'number', label: '数字' },
              { value: 'date', label: '日期' },
              { value: 'enum', label: '枚举' },
              { value: 'bool', label: '布尔' },
            ]}
            onChange={handleTypeChange}
            disabled={!!parameter}
          />
        </Form.Item>
        <Form.Item name="required" label="必填" valuePropName="checked">
          <Switch />
        </Form.Item>
        {paramType === 'enum' && (
          <Form.Item
            name="options"
            label="选项 (按回车添加)"
            rules={[{ required: true, message: '请至少添加一个选项' }]}
          >
            <Select mode="tags" placeholder="例如: east, west, north, south" />
          </Form.Item>
        )}
        <Form.Item name="default" label="默认值 (可选)">
          {paramType === 'string' && <Input allowClear placeholder="例如: hello" />}
          {paramType === 'number' && (
            <InputNumber style={{ width: '100%' }} placeholder="例如: 100" />
          )}
          {paramType === 'date' && <DatePicker style={{ width: '100%' }} />}
          {paramType === 'enum' && (
            <Select
              placeholder="从上方选项中选择"
              options={((form.getFieldValue('options') ?? []) as string[]).map((o) => ({
                value: o,
                label: o,
              }))}
              allowClear
            />
          )}
          {paramType === 'bool' && (
            <Select
              placeholder="默认状态"
              options={[
                { value: true, label: 'true' },
                { value: false, label: 'false' },
              ]}
              allowClear
            />
          )}
        </Form.Item>
      </Form>
    </Modal>
  );
}
