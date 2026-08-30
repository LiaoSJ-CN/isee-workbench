/**
 * Render a FieldChange value with semantic styling for the diff view.
 *
 * Replaces the original ``<code>{JSON.stringify(v)}</code>`` dump that
 * was unreadable for dict / list / multi-line string values. The
 * shapes come from :class:`backend.app.schemas.report_version.FieldChange`
 * so ``unknown`` is the honest type — anything not in this switch
 * falls through to JSON pretty-printing.
 *
 * Extracted out of ``DiffView.tsx`` so the rendering rules are
 * unit-testable in isolation.
 */
import type { ReactNode } from 'react';

const LONG_STRING_THRESHOLD = 80;
const PRE_MAX_HEIGHT = 240;

export function renderDiffValue(v: unknown): ReactNode {
  if (v === null || v === undefined) {
    return <span style={{ color: '#999' }}>—</span>;
  }
  if (typeof v === 'boolean') {
    return (
      <span style={{ color: v ? '#389e0d' : '#cf1322' }}>{v ? 'true' : 'false'}</span>
    );
  }
  if (typeof v === 'number') {
    return String(v);
  }
  if (typeof v === 'string') {
    if (v.length > LONG_STRING_THRESHOLD || v.includes('\n')) {
      return (
        <pre
          style={{
            margin: 0,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            fontSize: 12,
          }}
        >
          {v}
        </pre>
      );
    }
    return <code>{v}</code>;
  }
  const pretty = JSON.stringify(v, null, 2);
  return (
    <pre
      style={{
        margin: 0,
        fontSize: 12,
        maxHeight: PRE_MAX_HEIGHT,
        overflow: 'auto',
        background: '#fafafa',
        padding: 8,
        borderRadius: 4,
      }}
    >
      {pretty}
    </pre>
  );
}
