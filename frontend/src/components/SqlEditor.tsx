import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap, placeholder } from '@codemirror/view';
import { sql, SQLDialect } from '@codemirror/lang-sql';
import { autocompletion, completionKeymap } from '@codemirror/autocomplete';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { syntaxHighlighting, defaultHighlightStyle, HighlightStyle } from '@codemirror/language';
import { tags as t } from '@lezer/highlight';

// 定义 SQL 语言方言
const customSql = SQLDialect.define({
  keywords: 'select,from,where,and,or,order,by,group,having,limit,join,left,right,inner,outer,on,as,distinct,count,sum,avg,max,min,in,not,null,like,is,union,all,case,when,then,else,end,between,exists,cross,full,self',
});

// 使用默认高亮样式作为基础
const baseHighlight = defaultHighlightStyle;

// 创建一个自定义的补充高亮样式
const customHighlight = HighlightStyle.define([
  { tag: t.keyword, color: '#0000ff', fontWeight: 'bold' },
  { tag: t.string, color: '#22863a' },
  { tag: t.number, color: '#005cc5' },
  { tag: t.comment, color: '#6a737d', fontStyle: 'italic' },
  { tag: t.operator, color: '#d73a49' },
]);

// 注入自定义高亮 CSS
function injectStyles() {
  if (document.getElementById('sql-editor-styles')) return;
  const style = document.createElement('style');
  style.id = 'sql-editor-styles';
  // 这些类名对应 CodeMirror 的高亮标签
  // 关键字
  style.textContent = `
    .ͼ1 .ͼb { color: #0000ff !important; font-weight: bold !important; }
    .ͼ2 .ͼb { color: #79b8ff !important; font-weight: bold !important; }
    .ͼ3 .ͼb { color: #c792ea !important; font-weight: bold !important; }
    /* 字符串 */
    .ͼ1 .ͼc { color: #22863a !important; }
    .ͼ2 .ͼc { color: #9ecbff !important; }
    .ͼ3 .ͼc { color: #c1e1a3 !important; }
    /* 数字 */
    .ͼ1 .ͼd { color: #005cc5 !important; }
    .ͼ2 .ͼd { color: #f78c6c !important; }
    .ͼ3 .ͼd { color: #f78c6c !important; }
    /* 注释 */
    .ͼ1 .ͼe { color: #6a737d !important; font-style: italic !important; }
    .ͼ2 .ͼe { color: #676e95 !important; font-style: italic !important; }
    .ͼ3 .ͼe { color: #676e95 !important; font-style: italic !important; }
    /* 操作符 */
    .ͼ1 .ͼf { color: #d73a49 !important; }
    .ͼ2 .ͼf { color: #89ddff !important; }
    .ͼ3 .ͼf { color: #89ddff !important; }
  `;
  document.head.appendChild(style);
}

/** Imperative handle exposed via ``ref`` — lets parents insert text at
 * the cursor (used by the schema-browser sidebar). */
export interface SqlEditorHandle {
  insertAtCursor: (text: string) => void;
}

interface SqlEditorProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  height?: string;
}

const SqlEditor = forwardRef<SqlEditorHandle, SqlEditorProps>(function SqlEditor(
  { value, onChange, placeholder: placeholderText, height = '200px' },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);

  // 注入高亮样式
  useEffect(() => {
    injectStyles();
  }, []);

  // 创建编辑器
  useEffect(() => {
    if (!containerRef.current) return;

    const extensions = [
      history(),
      sql({ dialect: customSql }),
      // 使用多个高亮样式组合
      syntaxHighlighting(baseHighlight),
      syntaxHighlighting(customHighlight),
      autocompletion(),
      keymap.of([...defaultKeymap, ...historyKeymap, ...completionKeymap]),
      placeholder(placeholderText || 'SELECT * FROM ...'),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          onChange(update.state.doc.toString());
        }
      }),
      EditorView.theme({
        '&': { height, fontSize: '14px' },
        '.cm-scroller': { overflow: 'auto', fontFamily: 'monospace', lineHeight: '1.6' },
        '.cm-content': { caretColor: '#1890ff' },
        '.cm-line': { padding: '0 4px' },
      }),
    ];

    const state = EditorState.create({
      doc: value,
      extensions,
    });

    const view = new EditorView({
      state,
      parent: containerRef.current,
    });

    viewRef.current = view;

    return () => {
      view.destroy();
    };
    // CodeMirror 6 creates once on mount; re-creating on prop change
    // would lose cursor position, undo history, and selection state.
    // External value sync is handled by the separate effect below
    // (value → view.dispatch).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 同步外部值变化
  useEffect(() => {
    const view = viewRef.current;
    if (view && value !== view.state.doc.toString()) {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: value },
      });
    }
  }, [value]);

  // Expose imperative API for parents (schema-browser insertion).
  useImperativeHandle(
    ref,
    () => ({
      insertAtCursor: (text: string) => {
        const view = viewRef.current;
        if (!view) return;
        const sel = view.state.selection.main;
        view.dispatch({
          changes: { from: sel.from, to: sel.to, insert: text },
          selection: { anchor: sel.from + text.length },
        });
        view.focus();
      },
    }),
    [],
  );

  return (
    <div
      ref={containerRef}
      style={{
        border: '1px solid #d9d9d9',
        borderRadius: 6,
        overflow: 'hidden',
      }}
    />
  );
});

export default SqlEditor;