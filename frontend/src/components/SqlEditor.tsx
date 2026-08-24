import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { Compartment, EditorState } from '@codemirror/state';
import { EditorView, keymap, placeholder } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { syntaxHighlighting, defaultHighlightStyle, HighlightStyle } from '@codemirror/language';
import { tags as t } from '@lezer/highlight';

// 高亮样式（customHighlight 在 sql parser 加载前已生效，对 cm 内置 tag 仍可着色；
// 真正的 SQL 关键字/字符串等高亮要等 lang-sql 异步加载完成后才出现。）
const baseHighlight = defaultHighlightStyle;

const customHighlight = HighlightStyle.define([
  { tag: t.keyword, color: '#0000ff', fontWeight: 'bold' },
  { tag: t.string, color: '#22863a' },
  { tag: t.number, color: '#005cc5' },
  { tag: t.comment, color: '#6a737d', fontStyle: 'italic' },
  { tag: t.operator, color: '#d73a49' },
]);

// 注入高亮 CSS（lang-sql parser 加载前/后都用同一份）
function injectStyles() {
  if (document.getElementById('sql-editor-styles')) return;
  const style = document.createElement('style');
  style.id = 'sql-editor-styles';
  style.textContent = `
    .ͼ1 .ͼb { color: #0000ff !important; font-weight: bold !important; }
    .ͼ2 .ͼb { color: #79b8ff !important; font-weight: bold !important; }
    .ͼ3 .ͼb { color: #c792ea !important; font-weight: bold !important; }
    .ͼ1 .ͼc { color: #22863a !important; }
    .ͼ2 .ͼc { color: #9ecbff !important; }
    .ͼ3 .ͼc { color: #c1e1a3 !important; }
    .ͼ1 .ͼd { color: #005cc5 !important; }
    .ͼ2 .ͼd { color: #f78c6c !important; }
    .ͼ3 .ͼd { color: #f78c6c !important; }
    .ͼ1 .ͼe { color: #6a737d !important; font-style: italic !important; }
    .ͼ2 .ͼe { color: #676e95 !important; font-style: italic !important; }
    .ͼ3 .ͼe { color: #676e95 !important; font-style: italic !important; }
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

// 批 11.3 优化：把 ``@codemirror/lang-sql`` + ``@codemirror/autocomplete`` (共
// ~200KB raw / ~70KB gzip，SQL parser + 弹窗补全) 切成独立 chunk 并 lazy-load。
// 编辑器 shell（state / view / commands / language）立刻渲染，SQL 高亮和补全
// 在 ~50–150ms 后异步加载完接进来。三个 Compartment 让 ``reconfigure`` 不重建
// editor（保留 cursor / undo history / selection）。
const SqlEditor = forwardRef<SqlEditorHandle, SqlEditorProps>(function SqlEditor(
  { value, onChange, placeholder: placeholderText, height = '200px' },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);

  // Three Compartments so we can ``view.dispatch({ effects: reconfigure(...) })``
  // to add SQL language / autocompletion / completion keymaps without recreating
  // the editor (which would lose cursor position, undo history, selection).
  const langCompartment = useRef(new Compartment()).current;
  const complCompartment = useRef(new Compartment()).current;
  const keymapCompartment = useRef(new Compartment()).current;

  useEffect(() => {
    injectStyles();
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;

    const extensions = [
      history(),
      // 高亮样式立马生效（覆盖 cm 自带的 tag 高亮）
      syntaxHighlighting(baseHighlight),
      syntaxHighlighting(customHighlight),
      // 三处 Compartment：lang (sql + dialect) / compl (autocompletion) / keymap
      langCompartment.of([]),
      complCompartment.of([]),
      keymapCompartment.of(keymap.of([...defaultKeymap, ...historyKeymap])),
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 异步加载 SQL parser + autocompletion。fetch 完成（chunk 已在浏览器缓存则
  // 几乎瞬间完成）后用 ``reconfigure`` 注入三个 Compartment。用户先看到无 SQL
  // 关键字高亮的 editor，然后在 ms 级延迟后高亮与补全补上。
  useEffect(() => {
    let cancelled = false;
    Promise.all([import('@codemirror/lang-sql'), import('@codemirror/autocomplete')]).then(
      ([sqlMod, acMod]) => {
        if (cancelled) return;
        const view = viewRef.current;
        if (!view) return;
        const customSql = sqlMod.SQLDialect.define({
          keywords:
            'select,from,where,and,or,order,by,group,having,limit,join,left,right,inner,outer,on,as,distinct,count,sum,avg,max,min,in,not,null,like,is,union,all,case,when,then,else,end,between,exists,cross,full,self',
        });
        view.dispatch({
          effects: [
            langCompartment.reconfigure([sqlMod.sql({ dialect: customSql })]),
            complCompartment.reconfigure([acMod.autocompletion()]),
            keymapCompartment.reconfigure(
              keymap.of([...defaultKeymap, ...historyKeymap, ...acMod.completionKeymap]),
            ),
          ],
        });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [langCompartment, complCompartment, keymapCompartment]);

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
