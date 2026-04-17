'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  Plus,
  Play,
  Upload,
  Palette,
  CheckCircle,
  Warning,
  XCircle,
  Lightning,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type {
  Edge,
  FigmaImportSummary,
  Screen,
  TestPlan,
  UatRunSummary,
} from '@/lib/types'
import { ScreenUploader } from '@/components/ScreenUploader'
import { ScreenCard } from '@/components/ScreenCard'
import { FlowInferencePanel } from '@/components/FlowInferencePanel'
import { PlanTypeBadge } from '@/components/PlanTypeBadge'

export default function UATPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [screens, setScreens] = useState<Screen[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [plans, setPlans] = useState<TestPlan[]>([])
  const [runs, setRuns] = useState<UatRunSummary[]>([])
  const [figmaImports, setFigmaImports] = useState<FigmaImportSummary[]>([])
  const [figmaFileIdInput, setFigmaFileIdInput] = useState('rid4WC0zcs0yt3RjpST0dx')
  const [importing, setImporting] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [planFeature, setPlanFeature] = useState('')
  const [planFigmaId, setPlanFigmaId] = useState('')
  const [generatingPlan, setGeneratingPlan] = useState<string | null>(null)

  const refresh = async () => {
    try {
      const [s, e, pls, rs, fi] = await Promise.all([
        api.listScreens(projectId),
        api.listEdges(projectId),
        api.listPlans(projectId),
        api.listUatRuns(projectId),
        api.listFigmaImports(projectId),
      ])
      setScreens(s)
      setEdges(e)
      setPlans(pls)
      setRuns(rs)
      setFigmaImports(fi)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const triggerFigmaImport = async () => {
    if (!figmaFileIdInput.trim()) return
    setImporting(true)
    try {
      const imp = await api.createFigmaImport(projectId, figmaFileIdInput.trim())
      setFigmaImports((prev) => [imp, ...prev])
      if (imp.status === 'failed') {
        alert(`Import failed: ${(imp.error || '').slice(0, 300)}`)
      }
    } catch (err: any) {
      alert(`Import failed: ${err.message}`)
    } finally {
      setImporting(false)
    }
  }

  const generateSinglePlan = async (plan_type: string) => {
    if (!planFeature.trim()) {
      alert('Enter a feature description first.')
      return
    }
    if (plan_type === 'design_fidelity' && !planFigmaId.trim()) {
      alert('Design fidelity requires a Figma file ID.')
      return
    }
    setGeneratingPlan(plan_type)
    try {
      const newPlan = await api.createPlan(projectId, planFeature.trim(), {
        plan_type,
        figma_file_id: planFigmaId.trim() || undefined,
      })
      setPlans((prev) => [newPlan, ...prev])
      window.location.href = `/projects/${projectId}/plans/${newPlan.id}`
    } catch (err: any) {
      alert(`Plan generation failed: ${err.message}`)
    } finally {
      setGeneratingPlan(null)
    }
  }

  const generateSuite = async () => {
    if (!planFeature.trim()) {
      alert('Enter a feature description first.')
      return
    }
    setGeneratingPlan('suite')
    try {
      const newPlans = await api.createPlanSuite(
        projectId,
        planFeature.trim(),
        planFigmaId.trim() || undefined
      )
      setPlans((prev) => [...newPlans, ...prev])
      alert(
        `Generated ${newPlans.length} plans -- ${newPlans
          .map((p) => `${p.plan_type} (${p.cases.length})`)
          .join(', ')}`
      )
      setPlanFeature('')
    } catch (err: any) {
      alert(`Suite generation failed: ${err.message}`)
    } finally {
      setGeneratingPlan(null)
    }
  }

  useEffect(() => {
    refresh()
  }, [projectId])

  if (loading) return (
    <div className="space-y-4">
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <div className="skeleton h-5 w-32 mb-3" />
        <div className="skeleton h-4 w-full mb-2" />
        <div className="skeleton h-4 w-3/4" />
      </div>
    </div>
  )
  if (error) return <p className="text-red-400 text-sm">Error: {error}</p>

  return (
    <div>
      {/* Figma Imports */}
      <section className="mb-6 bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <div className="flex items-start justify-between mb-3">
          <div>
            <h2 className="text-lg font-medium flex items-center gap-2">
              <Palette size={18} className="text-emerald-400" />
              Figma Imports
            </h2>
            <p className="text-sm text-zinc-400 mt-1">
              One fetch = all UAT runs free. Designs stored locally, no Figma API
              calls on every run.
            </p>
          </div>
          {figmaImports.filter((i) => i.status === 'ready').length > 0 && (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-500/10 text-green-400 border border-green-500/20">
              <CheckCircle size={12} />
              {figmaImports.filter((i) => i.status === 'ready').length} ready
            </span>
          )}
        </div>

        {figmaImports.length === 0 ? (
          <div className="bg-zinc-950/60 rounded-lg p-3">
            <p className="text-xs text-zinc-500 mb-2">
              No imports yet. Paste a Figma file ID and click Import.
            </p>
            <div className="flex gap-2">
              <input
                type="text"
                value={figmaFileIdInput}
                onChange={(e) => setFigmaFileIdInput(e.target.value)}
                placeholder="e.g. rid4WC0zcs0yt3RjpST0dx"
                disabled={importing}
                className="flex-1 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm font-mono placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
              />
              <button
                onClick={triggerFigmaImport}
                disabled={importing || !figmaFileIdInput.trim()}
                className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors duration-150 active:scale-[0.98]"
              >
                <Upload size={14} />
                {importing ? 'Importing...' : 'Import'}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {figmaImports.slice(0, 4).map((imp) => {
              const statusColor =
                imp.status === 'ready'
                  ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                  : imp.status === 'fetching'
                  ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
                  : 'bg-red-500/10 text-red-400 border border-red-500/20'
              return (
                <div
                  key={imp.id}
                  className="border border-zinc-800 bg-zinc-950/60 rounded-lg p-3 flex items-center justify-between gap-3"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-sm">
                        {imp.file_name || 'Untitled file'}
                      </span>
                      <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${statusColor}`}>
                        {imp.status}
                      </span>
                      <span className="text-xs text-zinc-500">
                        {imp.total_frames} frames
                      </span>
                    </div>
                    <p className="text-xs text-zinc-600 font-mono truncate mt-0.5">
                      {imp.figma_file_id}
                    </p>
                    {imp.status === 'failed' && imp.error && (
                      <p className="text-xs text-red-300 mt-1 truncate">
                        {imp.error.split('\n')[0].slice(0, 140)}
                      </p>
                    )}
                  </div>
                  <span className="text-xs text-zinc-600 shrink-0">
                    {new Date(imp.imported_at).toLocaleDateString()}
                  </span>
                </div>
              )
            })}
            <div className="flex gap-2 mt-2">
              <input
                type="text"
                value={figmaFileIdInput}
                onChange={(e) => setFigmaFileIdInput(e.target.value)}
                placeholder="Figma file ID"
                disabled={importing}
                className="flex-1 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-xs font-mono placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
              />
              <button
                onClick={triggerFigmaImport}
                disabled={importing || !figmaFileIdInput.trim()}
                className="inline-flex items-center gap-1.5 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 hover:border-zinc-600 disabled:bg-zinc-800 disabled:text-zinc-500 text-zinc-300 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors duration-150 active:scale-[0.98]"
              >
                <Plus size={12} />
                {importing ? 'Importing...' : 'New import'}
              </button>
            </div>
          </div>
        )}
      </section>

      {/* UAT Runs */}
      <section className="mb-10 bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-lg font-medium flex items-center gap-2">
              <Play size={18} weight="fill" className="text-emerald-400" />
              UAT Runs
            </h2>
            <p className="text-sm text-zinc-400 mt-1">
              Install an APK, navigate the app autonomously, and get a Figma comparison report.
            </p>
          </div>
          <Link
            href={`/projects/${projectId}/runs/new`}
            className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg font-medium transition-colors duration-150 text-sm active:scale-[0.98] active:translate-y-[1px]"
          >
            <Plus size={14} weight="bold" />
            Start UAT run
          </Link>
        </div>

        {runs.length === 0 ? (
          <p className="text-sm text-zinc-500 text-center py-4">
            No runs yet. Start one above to execute your APK against the Figma spec.
          </p>
        ) : (
          <div className="space-y-2">
            {runs.slice(0, 5).map((r) => {
              const score = r.overall_match_score !== null ? `${(r.overall_match_score * 100).toFixed(0)}%` : '--'
              const statusColor = r.status === 'completed'
                ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                : r.status === 'failed'
                ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                : r.status === 'running'
                ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
                : 'bg-zinc-500/10 text-zinc-400 border border-zinc-500/20'
              return (
                <Link
                  key={r.id}
                  href={`/projects/${projectId}/runs/${r.id}`}
                  className="border border-zinc-800 bg-zinc-950/60 hover:border-zinc-700 hover:bg-zinc-900 rounded-lg p-3 flex items-center justify-between gap-3 transition-colors duration-200"
                >
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    <span className="font-medium text-sm">Run #{r.id}</span>
                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${statusColor}`}>{r.status}</span>
                    {r.apk_version && (
                      <span className="text-xs text-zinc-500 font-mono">v{r.apk_version}</span>
                    )}
                    <span className="text-xs text-zinc-600 flex items-center gap-2">
                      <span className="inline-flex items-center gap-0.5"><CheckCircle size={12} className="text-green-400" />{r.matched}</span>
                      <span className="inline-flex items-center gap-0.5"><Warning size={12} className="text-amber-400" />{r.mismatched}</span>
                      <span className="inline-flex items-center gap-0.5"><XCircle size={12} className="text-red-400" />{r.unreachable}</span>
                    </span>
                  </div>
                  <span className="text-lg font-semibold text-zinc-300">{score}</span>
                </Link>
              )
            })}
            {runs.length > 5 && (
              <Link
                href={`/projects/${projectId}/runs`}
                className="block text-center text-sm text-emerald-400 hover:text-emerald-300 pt-2 transition-colors duration-150"
              >
                View all {runs.length} runs
              </Link>
            )}
          </div>
        )}
      </section>

      {/* Bulk uploader */}
      <section className="mb-8">
        <h2 className="text-lg font-medium mb-3 flex items-center gap-2">
          <Upload size={18} className="text-zinc-400" />
          Upload screenshots
          <span className="text-xs text-zinc-500 font-normal ml-1">(optional -- helps bootstrap the app graph)</span>
        </h2>
        <ScreenUploader
          projectId={projectId}
          onUploaded={(newScreens) => setScreens((prev) => [...prev, ...newScreens])}
        />
      </section>

      {/* Flow inference */}
      {screens.length >= 2 && (
        <section className="mb-8">
          <h2 className="text-lg font-medium mb-3">2. Map the flow</h2>
          <FlowInferencePanel
            projectId={projectId}
            screens={screens}
            onEdgesAccepted={refresh}
          />
        </section>
      )}

      {/* Screens grid */}
      {screens.length > 0 && (
        <section>
          <h2 className="text-lg font-medium mb-3">
            Screens ({screens.length})
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {screens.map((s) => (
              <ScreenCard
                key={s.id}
                screen={s}
                onUpdated={(updated) =>
                  setScreens((prev) => prev.map((x) => (x.id === updated.id ? updated : x)))
                }
                onDeleted={(id) =>
                  setScreens((prev) => prev.filter((x) => x.id !== id))
                }
              />
            ))}
          </div>
        </section>
      )}

      {/* Test plans */}
      {screens.length > 0 && (
        <section className="mt-8">
          <h2 className="text-lg font-medium mb-3">3. Generate UAT plans</h2>
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 mb-4">
            <label className="block text-xs font-medium uppercase tracking-wider text-zinc-500 mb-2">
              Describe the feature you want to UAT
            </label>
            <textarea
              value={planFeature}
              onChange={(e) => setPlanFeature(e.target.value)}
              placeholder="e.g. We launched a new Hotel Details Page that shows photos, amenities, price per night, and a Book Now button"
              rows={3}
              className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
            />
            <label className="block text-xs font-medium uppercase tracking-wider text-zinc-500 mt-3 mb-2">
              Figma file ID <span className="normal-case tracking-normal text-zinc-600">(optional -- enables design fidelity plan)</span>
            </label>
            <input
              type="text"
              value={planFigmaId}
              onChange={(e) => setPlanFigmaId(e.target.value)}
              placeholder="e.g. rid4WC0zcs0yt3RjpST0dx"
              className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm font-mono placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
            />

            <div className="mt-4">
              <button
                onClick={generateSuite}
                disabled={!!generatingPlan || !planFeature.trim()}
                className="w-full inline-flex items-center justify-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white px-4 py-3 rounded-lg font-semibold text-sm transition-colors duration-150 active:scale-[0.98] active:translate-y-[1px]"
              >
                <Lightning size={16} weight="fill" />
                {generatingPlan === 'suite' ? 'Generating suite...' : 'Generate full UAT suite (all plan types)'}
              </button>
            </div>

            <div className="mt-4">
              <p className="text-xs text-zinc-500 mb-2">Or generate a single specialized plan:</p>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                {[
                  { type: 'design_fidelity', label: 'Design' },
                  { type: 'functional_flow', label: 'Functional' },
                  { type: 'deeplink_utility', label: 'Deeplink' },
                  { type: 'edge_cases', label: 'Edge cases' },
                  { type: 'feature_flow', label: 'Feature flow' },
                ].map(({ type, label }) => (
                  <button
                    key={type}
                    onClick={() => generateSinglePlan(type)}
                    disabled={!!generatingPlan || !planFeature.trim()}
                    className="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 hover:border-zinc-600 disabled:bg-zinc-900 disabled:text-zinc-600 text-zinc-200 px-3 py-2 rounded-lg text-xs font-medium transition-colors duration-150 active:scale-[0.98]"
                  >
                    {generatingPlan === type ? '...' : label}
                  </button>
                ))}
              </div>
            </div>
            <p className="text-xs text-zinc-600 mt-3">
              Tip: Telegram <code className="text-zinc-500 font-mono">/uatsuite &lt;description&gt;</code> also runs the full suite
            </p>
          </div>

          {plans.length > 0 && (
            <div className="space-y-2">
              {plans.map((p) => (
                <Link
                  key={p.id}
                  href={`/projects/${projectId}/plans/${p.id}`}
                  className="border border-zinc-800 bg-zinc-900 hover:border-zinc-700 rounded-xl p-3 text-sm flex items-center justify-between transition-colors duration-200 gap-3"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium">Plan #{p.id}</span>
                      <PlanTypeBadge type={p.plan_type} />
                      <span
                        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                          p.status === 'approved'
                            ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                            : 'bg-zinc-500/10 text-zinc-400 border border-zinc-500/20'
                        }`}
                      >
                        {p.status}
                      </span>
                      <span className="text-xs text-zinc-600">{p.cases.length} cases</span>
                    </div>
                    <p className="text-xs text-zinc-500 truncate mt-0.5 italic">
                      &quot;{p.feature_description}&quot;
                    </p>
                  </div>
                  <span className="text-xs text-zinc-600 shrink-0">
                    {new Date(p.created_at).toLocaleDateString()}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </section>
      )}

      {edges.length > 0 && (
        <section className="mt-8">
          <h2 className="text-lg font-medium mb-3">Edges ({edges.length})</h2>
          <div className="space-y-2">
            {edges.map((e) => {
              const from = screens.find((s) => s.id === e.from_screen_id)
              const to = screens.find((s) => s.id === e.to_screen_id)
              return (
                <div
                  key={e.id}
                  className="border border-zinc-800 bg-zinc-900 rounded-xl p-3 text-sm flex items-center justify-between"
                >
                  <div>
                    <span className="font-medium">{from?.display_name || from?.name}</span>
                    <span className="text-zinc-600 mx-2">&rarr;</span>
                    <span className="font-medium">{to?.display_name || to?.name}</span>
                    <span className="text-zinc-500 ml-3 text-xs">via {e.trigger}</span>
                  </div>
                  <button
                    onClick={async () => {
                      await api.deleteEdge(e.id)
                      setEdges((prev) => prev.filter((x) => x.id !== e.id))
                    }}
                    className="text-xs text-zinc-500 hover:text-red-400 transition-colors duration-150"
                  >
                    Remove
                  </button>
                </div>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}
