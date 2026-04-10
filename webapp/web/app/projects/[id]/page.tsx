'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/api'
import type { Edge, ProjectDetail, Screen, TestPlan } from '@/lib/types'
import { ScreenUploader } from '@/components/ScreenUploader'
import { ScreenCard } from '@/components/ScreenCard'
import { FlowInferencePanel } from '@/components/FlowInferencePanel'

export default function ProjectDetailPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [project, setProject] = useState<ProjectDetail | null>(null)
  const [screens, setScreens] = useState<Screen[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [plans, setPlans] = useState<TestPlan[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [planFeature, setPlanFeature] = useState('')
  const [generatingPlan, setGeneratingPlan] = useState(false)

  const refresh = async () => {
    try {
      const [p, s, e, pls] = await Promise.all([
        api.getProject(projectId),
        api.listScreens(projectId),
        api.listEdges(projectId),
        api.listPlans(projectId),
      ])
      setProject(p)
      setScreens(s)
      setEdges(e)
      setPlans(pls)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const generatePlan = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!planFeature.trim()) return
    setGeneratingPlan(true)
    try {
      const newPlan = await api.createPlan(projectId, planFeature.trim())
      setPlans((prev) => [newPlan, ...prev])
      setPlanFeature('')
      // Open the new plan
      window.location.href = `/projects/${projectId}/plans/${newPlan.id}`
    } catch (err: any) {
      alert(`Plan generation failed: ${err.message}`)
    } finally {
      setGeneratingPlan(false)
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
            <div className="text-2xl font-semibold">{screens.length}</div>
            <div className="text-xs text-zinc-500 uppercase tracking-wide">Screens</div>
          </div>
          <div>
            <div className="text-2xl font-semibold">{edges.length}</div>
            <div className="text-xs text-zinc-500 uppercase tracking-wide">Edges</div>
          </div>
        </div>
      </div>

      {/* Bulk uploader */}
      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-3">1. Upload screenshots</h2>
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
          <form
            onSubmit={generatePlan}
            className="border border-zinc-800 bg-zinc-900/30 rounded-lg p-4 mb-4"
          >
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
            <div className="flex items-center justify-between mt-3">
              <p className="text-xs text-zinc-600">
                Tip: you can also send <code className="text-zinc-500">/uat &lt;description&gt;</code> from Telegram
              </p>
              <button
                type="submit"
                disabled={generatingPlan || !planFeature.trim()}
                className="bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white px-4 py-2 rounded-md font-medium text-sm"
              >
                {generatingPlan ? 'Generating…' : '✨ Generate plan'}
              </button>
            </div>
          </form>

          {plans.length > 0 && (
            <div className="space-y-2">
              {plans.map((p) => (
                <Link
                  key={p.id}
                  href={`/projects/${projectId}/plans/${p.id}`}
                  className="border border-zinc-800 bg-zinc-900/50 hover:border-zinc-700 hover:bg-zinc-900 rounded p-3 text-sm flex items-center justify-between transition"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">Plan #{p.id}</span>
                      <span
                        className={`text-xs px-1.5 py-0.5 rounded ${
                          p.status === 'approved'
                            ? 'bg-emerald-950 text-emerald-300'
                            : 'bg-zinc-800 text-zinc-400'
                        }`}
                      >
                        {p.status}
                      </span>
                    </div>
                    <p className="text-xs text-zinc-500 truncate mt-0.5 italic">
                      "{p.feature_description}"
                    </p>
                  </div>
                  <span className="text-xs text-zinc-600 ml-3 shrink-0">
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
