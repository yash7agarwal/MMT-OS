'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/api'
import type { Screen, TestCase, TestPlan } from '@/lib/types'
import { TestCaseCard } from '@/components/TestCaseCard'

export default function PlanReviewPage({
  params,
}: {
  params: { id: string; planId: string }
}) {
  const projectId = parseInt(params.id, 10)
  const planId = parseInt(params.planId, 10)
  const [plan, setPlan] = useState<TestPlan | null>(null)
  const [screens, setScreens] = useState<Screen[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    try {
      const [p, s] = await Promise.all([
        api.getPlan(planId),
        api.listScreens(projectId),
      ])
      setPlan(p)
      setScreens(s)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [planId, projectId])

  const updateCase = (updated: TestCase) => {
    setPlan((p) =>
      p ? { ...p, cases: p.cases.map((c) => (c.id === updated.id ? updated : c)) } : p
    )
  }

  const removeCase = (id: number) => {
    setPlan((p) => (p ? { ...p, cases: p.cases.filter((c) => c.id !== id) } : p))
  }

  const groupedByBranch = useMemo(() => {
    if (!plan) return {}
    const groups: Record<string, TestCase[]> = {}
    plan.cases.forEach((c) => {
      const key = c.branch_label || 'Default'
      groups[key] = groups[key] || []
      groups[key].push(c)
    })
    return groups
  }, [plan])

  const stats = useMemo(() => {
    if (!plan) return { total: 0, approved: 0, removed: 0 }
    return {
      total: plan.cases.length,
      approved: plan.cases.filter((c) => c.status === 'approved').length,
      removed: plan.cases.filter((c) => c.status === 'removed').length,
    }
  }, [plan])

  const approveAll = async () => {
    if (!plan) return
    const proposals = plan.cases.filter((c) => c.status === 'proposed')
    for (const c of proposals) {
      const updated = await api.updateCase(c.id, { status: 'approved' })
      updateCase(updated)
    }
  }

  const finalizePlan = async () => {
    if (!plan) return
    const updated = await api.approvePlan(plan.id)
    setPlan({ ...plan, status: updated.status })
  }

  if (loading) return <p className="text-zinc-500">Loading…</p>
  if (error) return <p className="text-red-400">Error: {error}</p>
  if (!plan) return <p className="text-zinc-500">Plan not found</p>

  return (
    <div>
      <Link
        href={`/projects/${projectId}`}
        className="text-zinc-500 hover:text-zinc-300 text-sm mb-4 inline-block"
      >
        ← Back to project
      </Link>

      <div className="flex items-start justify-between mb-6 gap-6">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-2xl font-bold">Test Plan #{plan.id}</h1>
            <span
              className={`text-xs px-2 py-0.5 rounded ${
                plan.status === 'approved'
                  ? 'bg-emerald-950 text-emerald-300 border border-emerald-900'
                  : 'bg-zinc-800 text-zinc-400'
              }`}
            >
              {plan.status}
            </span>
          </div>
          <p className="text-zinc-400 italic">"{plan.feature_description}"</p>
          <p className="text-xs text-zinc-600 mt-1">
            Created {new Date(plan.created_at).toLocaleString()}
          </p>
        </div>
        <div className="flex gap-6 text-center shrink-0">
          <div>
            <div className="text-2xl font-semibold">{stats.total}</div>
            <div className="text-xs text-zinc-500 uppercase">Total</div>
          </div>
          <div>
            <div className="text-2xl font-semibold text-emerald-400">{stats.approved}</div>
            <div className="text-xs text-zinc-500 uppercase">Approved</div>
          </div>
          <div>
            <div className="text-2xl font-semibold text-zinc-500">{stats.removed}</div>
            <div className="text-xs text-zinc-500 uppercase">Removed</div>
          </div>
        </div>
      </div>

      {/* Bulk actions */}
      {plan.status !== 'approved' && stats.approved < stats.total - stats.removed && (
        <div className="border border-zinc-800 bg-zinc-900/30 rounded-lg p-3 mb-6 flex items-center justify-between">
          <span className="text-sm text-zinc-400">
            {stats.total - stats.approved - stats.removed} case
            {stats.total - stats.approved - stats.removed !== 1 ? 's' : ''} pending review
          </span>
          <button
            onClick={approveAll}
            className="bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded text-sm font-medium"
          >
            ✓ Approve all
          </button>
        </div>
      )}

      {/* Cases grouped by branch */}
      <div className="space-y-6">
        {Object.entries(groupedByBranch).map(([branch, cases]) => (
          <section key={branch}>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 mb-3">
              {branch} <span className="text-zinc-700">({cases.length})</span>
            </h2>
            <div className="space-y-3">
              {cases.map((c) => (
                <TestCaseCard
                  key={c.id}
                  testCase={c}
                  screens={screens}
                  onUpdated={updateCase}
                  onDeleted={removeCase}
                />
              ))}
            </div>
          </section>
        ))}
      </div>

      {/* Finalize */}
      {plan.status !== 'approved' && stats.approved > 0 && (
        <div className="mt-8 border border-emerald-900 bg-emerald-950/20 rounded-lg p-4 flex items-center justify-between">
          <div>
            <p className="font-medium text-emerald-300">Ready to finalize?</p>
            <p className="text-xs text-zinc-400 mt-1">
              Marks the plan as approved. The CLI can then pick it up to execute.
            </p>
          </div>
          <button
            onClick={finalizePlan}
            className="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded font-medium"
          >
            Finalize plan
          </button>
        </div>
      )}
    </div>
  )
}
