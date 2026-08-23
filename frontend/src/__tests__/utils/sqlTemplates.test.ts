/** Tests for :file:`src/utils/sqlTemplates.ts` (批 10.2).

Pure-function tests — no DOM, no antd. Covers the prefix-based
``categoryOf`` rule + the ``groupTemplatesByCategory`` aggregator
that the dropdown's OptGroup tree renders.
*/

import { describe, expect, it } from 'vitest'

import {
  DEFAULT_TEMPLATES,
  categoryOf,
  groupTemplatesByCategory,
  type SavedTemplate,
} from '../../utils/sqlTemplates'

describe('categoryOf', () => {
  it('puts dim_* templates in 维度表', () => {
    expect(categoryOf({ id: 'dim_supplier' })).toBe('dim')
    expect(categoryOf({ id: 'dim_customer' })).toBe('dim')
  })

  it('puts dwd_* templates in 业务明细', () => {
    expect(categoryOf({ id: 'dwd_voucher' })).toBe('dwd')
    expect(categoryOf({ id: 'dwd_ar_balance' })).toBe('dwd')
  })

  it('puts dws_* and ads_* templates in 聚合分析', () => {
    expect(categoryOf({ id: 'dws_ar_aging' })).toBe('aggregate')
    expect(categoryOf({ id: 'ads_cashflow' })).toBe('aggregate')
  })

  it('puts cross_* templates in 跨表 JOIN', () => {
    expect(categoryOf({ id: 'cross_ar_by_region' })).toBe('cross')
  })

  it('falls back to custom when no prefix matches', () => {
    expect(categoryOf({ id: 'my_analyst_query' })).toBe('custom')
    expect(categoryOf({ id: 'untitled-2026' })).toBe('custom')
    expect(categoryOf({ id: 'dwdish' })).toBe('custom') // startsWith, not contains
  })
})

describe('groupTemplatesByCategory', () => {
  it('produces 4 non-empty buckets for the 19 built-in templates', () => {
    const groups = groupTemplatesByCategory(DEFAULT_TEMPLATES)
    const labels = groups.map((g) => g.category.label)
    expect(labels).toEqual(['维度表', '业务明细', '聚合分析', '跨表 JOIN'])
  })

  it('keeps each built-in in exactly one bucket (no duplicates)', () => {
    const groups = groupTemplatesByCategory(DEFAULT_TEMPLATES)
    const allIds = groups.flatMap((g) => g.templates.map((t) => t.id))
    expect(new Set(allIds).size).toBe(allIds.length)
    expect(allIds.length).toBe(DEFAULT_TEMPLATES.length)
  })

  it('appends 自定义 only when user-created templates are present', () => {
    const userOnly = groupTemplatesByCategory([
      { id: 'note_a', name: 'A', sql: 'SELECT 1' },
      { id: 'note_b', name: 'B', sql: 'SELECT 2' },
    ])
    expect(userOnly.map((g) => g.category.label)).toEqual(['自定义'])

    const builtinsOnly = groupTemplatesByCategory(DEFAULT_TEMPLATES)
    expect(builtinsOnly.map((g) => g.category.label)).not.toContain('自定义')

    const mixed = groupTemplatesByCategory([
      ...DEFAULT_TEMPLATES,
      { id: 'mine', name: 'My query', sql: 'SELECT 3' },
    ])
    expect(mixed.map((g) => g.category.label).at(-1)).toBe('自定义')
  })

  it('skips empty built-in buckets instead of rendering empty OptGroups', () => {
    // Only user-created templates — none of the built-in prefixes
    // match, so the built-in buckets should be omitted entirely.
    const groups = groupTemplatesByCategory([
      { id: 'q1', name: 'Q1', sql: 'SELECT 1' },
    ])
    expect(groups).toHaveLength(1)
    expect(groups[0].category.label).toBe('自定义')
  })

  it('preserves the source order within a bucket', () => {
    const groups = groupTemplatesByCategory(DEFAULT_TEMPLATES)
    const dimGroup = groups.find((g) => g.category.id === 'dim')!
    expect(dimGroup.templates.map((t) => t.id)).toEqual([
      'dim_supplier',
      'dim_customer',
      'dim_department',
      'dim_cost_center',
      'dim_account',
    ])
  })
})

describe('DEFAULT_TEMPLATES sanity', () => {
  it('has 18 entries (5 DIM + 6 DWD + 4 DWS/ADS + 3 cross)', () => {
    expect(DEFAULT_TEMPLATES).toHaveLength(18)
  })

  it('every entry has unique id (no OptGroup key collisions)', () => {
    const ids = DEFAULT_TEMPLATES.map((t: SavedTemplate) => t.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})