import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { renderDiffValue } from '../../utils/diffValue';

describe('renderDiffValue', () => {
  it('renders null/undefined as an em-dash placeholder', () => {
    const { container: c1 } = render(<>{renderDiffValue(null)}</>);
    expect(c1.textContent).toBe('—');

    const { container: c2 } = render(<>{renderDiffValue(undefined)}</>);
    expect(c2.textContent).toBe('—');
  });

  it('renders booleans with semantic colors', () => {
    const { container: trueC } = render(<>{renderDiffValue(true)}</>);
    expect(trueC.textContent).toBe('true');
    // ``#389e0d`` is antd's green-7; happy-dom keeps the inline style
    // verbatim so we match the raw hex.
    expect(trueC.querySelector('span')?.getAttribute('style')).toContain('#389e0d');

    const { container: falseC } = render(<>{renderDiffValue(false)}</>);
    expect(falseC.textContent).toBe('false');
    expect(falseC.querySelector('span')?.getAttribute('style')).toContain('#cf1322');
  });

  it('renders numbers as plain text', () => {
    const { container } = render(<>{renderDiffValue(42)}</>);
    expect(container.textContent).toBe('42');
  });

  it('renders short strings inline in <code>', () => {
    const { container } = render(<>{renderDiffValue('Q1 报表')}</>);
    expect(container.querySelector('code')).not.toBeNull();
    expect(container.textContent).toBe('Q1 报表');
  });

  it('renders long strings in a <pre> block (preserves newlines)', () => {
    const longSql = 'SELECT *\nFROM orders\nWHERE region = \'CN\'\nLIMIT 100;';
    const { container } = render(<>{renderDiffValue(longSql)}</>);
    const pre = container.querySelector('pre');
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toBe(longSql);
    // Not wrapped in <code>.
    expect(container.querySelector('code')).toBeNull();
  });

  it('renders objects / arrays as pretty JSON in a <pre> block', () => {
    const obj = { a: 1, nested: { x: 'y' } };
    const { container } = render(<>{renderDiffValue(obj)}</>);
    const pre = container.querySelector('pre');
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain('"a": 1');
    expect(pre?.textContent).toContain('"nested"');
  });

  it('renders arrays with their bracket syntax', () => {
    const arr = ['csv', 'pdf'];
    const { container } = render(<>{renderDiffValue(arr)}</>);
    expect(container.querySelector('pre')?.textContent).toContain('[\n  "csv"');
  });
});
