import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Proxy /api requests to the backend during development.
      // The nginx reverse proxy handles this in production.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    // 批 10 — manualChunks splits node_modules into route-shared vendor
    // chunks so the initial bundle stays under the 500 KB warning
    // threshold. Route-local vendors (cm-vendor, dnd-vendor) only load
    // when a page that needs them is opened. The matcher is a function
    // (not a regex map) so each module is bucketed exactly once — no
    // accidental double-bundling across chunks.
    //
    // ``antd-vendor`` still weighs ~1.2 MB raw / ~370 KB gzip after
    // icons are peeled off — every page imports antd, so the whole
    // surface is a true shared dependency and cannot be split further
    // without losing tree-shaking. Bumping the warning threshold to
    // 1300 KB (well above any single chunk we emit, well below the 2 MB
    // raw that the SPA had before code-splitting) keeps the build log
    // quiet without hiding the real signal: the entry index chunk is
    // 15 KB raw / 5 KB gzip.
    chunkSizeWarningLimit: 1300,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          // Route-local: only the consumers below need these; pull in
          // with the page chunk rather than the initial bundle.
          // 批 11.3: split the CodeMirror surface into two route-local
          // chunks. ``cm-sql`` (the heavy SQL parser + autocompletion,
          // ~200 KB raw / ~70 KB gzip) is lazy-imported from SqlEditor
          // via ``import('@codemirror/lang-sql')`` after the editor
          // shell has rendered. ``cm-vendor`` keeps the editor itself
          // (state/view/commands/language + lezer highlight core),
          // ~80-100 KB raw / ~30 KB gzip. Initial /explorer navigation
          // gets a usable editor in one HTTP round-trip; SQL syntax
          // highlighting and the autocomplete popup are added ~50-150ms
          // later as a second chunk.
          if (
            id.includes('@codemirror/lang-sql') ||
            id.includes('@codemirror/autocomplete')
          ) {
            return 'cm-sql'
          }
          if (id.includes('@codemirror') || id.includes('@lezer/highlight')) {
            return 'cm-vendor'
          }
          if (id.includes('@dnd-kit')) {
            return 'dnd-vendor'
          }
          // Shared across the app — loaded once with the entry chunk.
          // Icons live in their own chunk because most icons are only
          // referenced from one or two pages (the few shared ones like
          // FundOutlined in the AppShell tree-shake into the entry
          // chunk anyway). Splitting icons out of antd-vendor drops the
          // shared chunk by ~300 KB raw / ~80 KB gzip.
          if (id.includes('@ant-design/icons')) {
            return 'icons-vendor'
          }
          if (id.includes('@ant-design') || /[/\\]antd[/\\]/.test(id)) {
            return 'antd-vendor'
          }
          if (id.includes('@tanstack/react-query')) {
            return 'rq-vendor'
          }
          if (id.includes('react-router')) {
            return 'router-vendor'
          }
          if (id.includes('dayjs')) {
            return 'dayjs-vendor'
          }
          if (/[/\\](react|react-dom|scheduler)[/\\]/.test(id)) {
            return 'react-vendor'
          }
          // Anything else from node_modules (axios, rc-*, misc) — keep
          // in a generic bucket so it's still cacheable across pages.
          return 'vendor'
        },
      },
    },
  },
})
