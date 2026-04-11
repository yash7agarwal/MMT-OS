'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/api'
import type {
  Edge,
  FigmaImportSummary,
  ProjectDetail,
  Screen,
  TestPlan,
  UatRunSummary,
} from '@/lib/types'
import { ScreenUploader } from '@/components/ScreenUploader'
import { ScreenCard } from '@/components/ScreenCard'
import { FlowInferencePanel } from '@/components/FlowInferencePanel'
import { PlanTypeBadge } from '@/components/PlanTypeBadge'

export default function ProjectDetailPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [project, setProject] = useState<ProjectDetail | null>(null)
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
      const [p, s, e, pls, rs, fi] = await Promise.all([
        api.getProject(projectId),
        api.listScreens(projectId),
        api.listEdges(projectId),
        api.listPlans(projectId),
        api.listUatRuns(projectId),
        api.listFigmaImports(projectId),
      ])
      setProject(p)
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
        `Generated ${newPlans.length} plans — ${newPlans
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

  if (loading) return <p className="text-zinc-500">Loading…</p>
  if (error) return <p className="text-red-400">Error: {error}</p>
  if (!project) return <p className="text-zinc-500">Project not found</p>

  return (
    <div>
      <Link href="/" className="text-zinc-500 hover:text-zinc-300 text-sm mb-4 inline-block">
        ← All projects
      </Link>

      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold">{project.name}</h1>
          {project.app_package && (
            <p className="text-zinc-500 font-mono text-sm mt-1">{project.app_package}</p>
          )}
          {project.description && (
            <p className="text-zinc-400 mt-2 max-w-2xl">{project.description}</p>
          )}
        </div>
        <div className="flex gap-6 text-center">
          <div>
            <div className="text-2xl font-semibold">{runs.length}</div>
            <div className="text-xs text-zinc-500 uppercase tracking-wide">Runs</div>
          </div>
          <div>
            <div className="text-2xl font-semibold">{screens.length}</div>
            <div className="text-xs text-zinc-500 uppercase tracking-wide">Screens</div>
          </div>
          <div>
            <div className="text-2xl font-semibold">{edges.length}</div>
            <div className="text-xs text-zinc-500 uppercase tracking-wide">Edges</div>
          </div>
        </div>
      </div>

      {/* ──────────── FIGMA IMPORTS (source of truth) ──────────── */}
      <section className="mb-6 border border-purple-900 bg-purple-950/20 rounded-lg p-5">
        <div className="flex items-start justify-between mb-3">
          <div>
            <h2 className="text-lg font-bold">🎨 Figma Imports</h2>
            <p className="text-sm text-zinc-400 mt-1">
              One fetch = all UAT runs free. Designs stored locally, no Figma API
              calls on every run.
            </p>
          </div>
          {figmaImports.filter((i) => i.status === 'ready').length > 0 && (
            <span className="text-xs text-emerald-400">
              ✓ {figmaImports.filter((i) => i.status === 'ready').length} ready
            </span>
          )}
        </div>

        {figmaImports.length === 0 ? (
          <div className="bg-zinc-950/60 rounded p-3">
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
                className="flex-1 bg-zinc-900 border border-zinc-800 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:border-purple-500"
              />
              <button
                onClick={triggerFigmaImport}
                disabled={importing || !figmaFileIdInput.trim()}
                className="bg-purple-600 hover:bg-purple-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white px-4 py-2 rounded text-sm font-semibold"
              >
                {importing ? 'Importing…' : 'Import'}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {figmaImports.slice(0, 4).map((imp) => {
              const statusColor =
                imp.status === 'ready'
                  ? 'bg-emerald-950 text-emerald-300 border-emerald-900'
                  : imp.status === 'fetching'
                  ? 'bg-blue-950 text-blue-300 border-blue-900'
                  : 'bg-red-950 text-red-300 border-red-900'
              return (
                <div
                  key={imp.id}
                  className="border border-zinc-800 bg-zinc-950/60 rounded p-3 flex items-center justify-between gap-3"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-sm">
                        {imp.file_name || 'Untitled file'}
                      </span>
                      <span className={`text-xs px-2 py-0.5 rounded border ${statusColor}`}>
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
                className="flex-1 bg-zinc-900 border border-zinc-800 rounded px-3 py-1.5 text-xs font-mono focus:outline-none focus:border-purple-500"
              />
              <button
                onClick={triggerFigmaImport}
                disabled={importing || !figmaFileIdInput.trim()}
                className="bg-purple-700 hover:bg-purple-600 disabled:bg-zinc-800 disabled:text-zinc-500 text-white px-3 py-1.5 rounded text-xs font-medium"
              >
                {importing ? 'Importing…' : '+ New import'}
              </button>
            </div>
          </div>
        )}
      </section>

      {/* ──────────── UAT RUNS (primary) ──────────── */}
      <section className="mb-10 border border-indigo-900 bg-indigo-950/20 rounded-lg p-5">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-xl font-bold">▶ UAT Runs</h2>
            <p className="text-sm text-zinc-400 mt-1">
              Install an APK, navigate the app autonomously, and get a Figma comparison report.
            </p>
          </div>
          <Link
            href={`/projects/${projectId}/runs/new`}
            className="bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-md font-semibold transition text-sm"
          >
            + Start UAT run
          </Link>
        </div>

        {runs.length === 0 ? (
          <p className="text-sm text-zinc-500 text-center py-4">
            No runs yet. Start one above to execute your APK against the Figma spec.
          </p>
        ) : (
          <div className="space-y-2">
            {runs.slice(0, 5).map((r) => {
              const score = r.overall_match_score !== null ? `${(r.overall_match_score * 100).toFixed(0)}%` : '—'
              const statusColor = r.status === 'completed'
                ? 'bg-emerald-950 text-emerald-300'
                : r.status === 'failed'
                ? 'bg-red-950 text-red-300'
                : r.status === 'running'
                ? 'bg-blue-950 text-blue-300'
                : 'bg-zinc-800 text-zinc-400'
              return (
                <Link
                  key={r.id}
                  href={`/projects/${projectId}/runs/${r.id}`}
                  className="border border-zinc-800 bg-zinc-950/60 hover:border-zinc-700 hover:bg-zinc-900 rounded p-3 flex items-center justify-between gap-3 transition"
                >
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    <span className="font-medium text-sm">Run #{r.id}</span>
                    <span className={`text-xs px-2 py-0.5 rounded ${statusColor}`}>{r.status}</span>
                    {r.apk_version && (
                      <span className="text-xs text-zinc-500 font-mono">v{r.apk_version}</span>
                    )}
                    <span className="text-xs text-zinc-600">
                      ✅{r.matched} ⚠️{r.mismatched} ❌{r.unreachable}
                    </span>
                  </div>
                  <span className="text-lg font-bold text-zinc-300">{score}</span>
                </Link>
              )
            })}
            {runs.length > 5 && (
              <Link
                href={`/projects/${projectId}/runs`}
                className="block text-center text-sm text-indigo-400 hover:text-indigo-300 pt-2"
              >
                View all {runs.length} runs →
              </Link>
            )}
          </div>
        )}
      </section>

      {/* Bulk uploader (secondary — for bootstrapping the screen map) */}
      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-3">
          Upload screenshots
          <span className="text-xs text-zinc-500 font-normal ml-2">(optional — helps bootstrap the app graph)</span>
        </h2>
        <ScreenUploader
          projectId={projectId}
          onUploaded={(newScreens) => setScreens((prev) => [...prev, ...newScreens])}
        />
      </section>

      {/* Flow inference */}
      {screens.length >= 2 && (
        <section className="mb-8">
          <h2 className="text-lg font-semibold mb-3">2. Map the flow</h2>
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
          <h2 className="text-lg font-semibold mb-3">
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
          <h2 className="text-lg font-semibold mb-3">3. Generate UAT plans</h2>
          <div className="border border-zinc-800 bg-zinc-900/30 rounded-lg p-4 mb-4">
            <label className="block text-sm text-zinc-400 mb-2">
              Describe the feature you want to UAT
            </label>
            <textarea
              value={planFeature}
              onChange={(e) => setPlanFeature(e.target.value)}
              placeholder="e.g. We launched a new Hotel Details Page that shows photos, amenities, price per night, and a Book Now button"
              rows={3}
              className="w-full bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-indigo-500"
            />
            <label className="block text-sm text-zinc-400 mt-3 mb-2">
              Figma file ID <span className="text-zinc-600">(optional — enables design fidelity plan)</span>
            </label>
            <input
              type="text"
              value={planFigmaId}
              onChange={(e) => setPlanFigmaId(e.target.value)}
              placeholder="e.g. rid4WC0zcs0yt3RjpST0dx"
              className="w-full bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 text-sm font-mono focus:outline-none focus:border-indigo-500"
            />

            <div className="mt-4">
              <button
                onClick={generateSuite}
                disabled={!!generatingPlan || !planFeature.trim()}
                className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white px-4 py-3 rounded-md font-semibold text-sm transition"
              >
                {generatingPlan === 'suite' ? 'Generating suite…' : '✨ Generate full UAT suite (all plan types)'}
              </button>
            </div>

            <div className="mt-4">
              <p className="text-xs text-zinc-500 mb-2">Or generate a single specialized plan:</p>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                {[
                  { type: 'design_fidelity', label: '🎨 Design' },
                  { type: 'functional_flow', label: '🔀 Functional' },
                  { type: 'deeplink_utility', label: '🔗 Deeplink' },
                  { type: 'edge_cases', label: '⚠️ Edge cases' },
                  { type: 'feature_flow', label: '📋 Feature flow' },
                ].map(({ type, label }) => (
                  <button
                    key={type}
                    onClick={() => generateSinglePlan(type)}
                    disabled={!!generatingPlan || !planFeature.trim()}
                    className="bg-zinc-800 hover:bg-zinc-700 disabled:bg-zinc-900 disabled:text-zinc-600 text-zinc-200 px-3 py-2 rounded text-xs font-medium transition"
                  >
                    {generatingPlan === type ? '…' : label}
                  </button>
                ))}
              </div>
            </div>
            <p className="text-xs text-zinc-600 mt-3">
              Tip: Telegram <code className="text-zinc-500">/uatsuite &lt;description&gt;</code> also runs the full suite
            </p>
          </div>

          {plans.length > 0 && (
            <div className="space-y-2">
              {plans.map((p) => (
                <Link
                  key={p.id}
                  href={`/projects/${projectId}/plans/${p.id}`}
                  className="border border-zinc-800 bg-zinc-900/50 hover:border-zinc-700 hover:bg-zinc-900 rounded p-3 text-sm flex items-center justify-between transition gap-3"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium">Plan #{p.id}</span>
                      <PlanTypeBadge type={p.plan_type} />
                      <span
                        className={`text-xs px-1.5 py-0.5 rounded ${
                          p.status === 'approved'
                            ? 'bg-emerald-950 text-emerald-300'
                            : 'bg-zinc-800 text-zinc-400'
                        }`}
                      >
                        {p.status}
                      </span>
                      <span className="text-xs text-zinc-600">{p.cases.length} cases</span>
                    </div>
                    <p className="text-xs text-zinc-500 truncate mt-0.5 italic">
                      "{p.feature_description}"
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
          <h2 className="text-lg font-semibold mb-3">Edges ({edges.length})</h2>
          <div className="space-y-2">
            {edges.map((e) => {
              const from = screens.find((s) => s.id === e.from_screen_id)
              const to = screens.find((s) => s.id === e.to_screen_id)
              return (
                <div
                  key={e.id}
                  className="border border-zinc-800 bg-zinc-900/50 rounded p-3 text-sm flex items-center justify-between"
                >
                  <div>
                    <span className="font-medium">{from?.display_name || from?.name}</span>
                    <span className="text-zinc-600 mx-2">→</span>
                    <span className="font-medium">{to?.display_name || to?.name}</span>
                    <span className="text-zinc-500 ml-3 text-xs">via {e.trigger}</span>
                  </div>
                  <button
                    onClick={async () => {
                      await api.deleteEdge(e.id)
                      setEdges((prev) => prev.filter((x) => x.id !== e.id))
                    }}
                    className="text-xs text-zinc-500 hover:text-red-400"
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
