'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  Buildings,
  MagnifyingGlass,
  ArrowRight,
  Sparkle,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { KnowledgeEntity } from '@/lib/types'

// v0.20.2: depth label by finding count. Matches backend bands in
// webapp/api/routes/knowledge.py:list_competitors.
function depthLabel(count: number): { pct: number; label: string; tone: string } {
  if (count === 0) return { pct: 10, label: 'No findings', tone: 'text-zinc-500 bg-zinc-800/40 border-zinc-700' }
  if (count <= 2) return { pct: 30, label: 'Shallow', tone: 'text-amber-400 bg-amber-500/10 border-amber-500/20' }
  if (count <= 4) return { pct: 60, label: 'Medium', tone: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/20' }
  if (count <= 7) return { pct: 90, label: 'Deep', tone: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20' }
  return { pct: 100, label: 'Comprehensive', tone: 'text-emerald-300 bg-emerald-500/15 border-emerald-500/30' }
}

export default function CompetitorsPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [competitors, setCompetitors] = useState<KnowledgeEntity[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deepening, setDeepening] = useState<Record<number, boolean>>({})
  const [flash, setFlash] = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      try {
        const data = await api.listCompetitors(projectId)
        setCompetitors(data)
      } catch (err: any) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [projectId])

  const handleDeepen = async (e: React.MouseEvent, entityId: number, name: string) => {
    e.preventDefault()  // suppress card-link navigation
    e.stopPropagation()
    setDeepening(prev => ({ ...prev, [entityId]: true }))
    try {
      const r = await api.deepenCompetitor(entityId)
      setFlash(r.created
        ? `Queued deep profile for ${name}. The intel agent will pick it up — refresh in 1–2 min.`
        : `${name} already has a pending deep-profile job (${r.reason || 'in queue'}).`)
      setTimeout(() => setFlash(null), 6000)
    } catch (err: any) {
      setFlash(`Failed to queue ${name}: ${err.message || err}`)
      setTimeout(() => setFlash(null), 8000)
    } finally {
      setDeepening(prev => ({ ...prev, [entityId]: false }))
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-zinc-950 p-6 md:p-10">
        <div className="max-w-5xl mx-auto">
          <div className="h-8 w-48 bg-zinc-800 rounded animate-pulse mb-6" />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
                <div className="h-5 w-40 bg-zinc-800 rounded animate-pulse mb-3" />
                <div className="h-4 w-full bg-zinc-800 rounded animate-pulse mb-2" />
                <div className="h-4 w-3/4 bg-zinc-800 rounded animate-pulse mb-4" />
                <div className="flex gap-2">
                  <div className="h-6 w-20 bg-zinc-800 rounded-full animate-pulse" />
                  <div className="h-6 w-16 bg-zinc-800 rounded-full animate-pulse" />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-zinc-950 p-6 md:p-10">
        <p className="text-red-400 text-sm">Error: {error}</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-zinc-950 p-6 md:p-10">
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <Buildings size={24} className="text-emerald-400" />
            <h1 className="text-2xl font-semibold tracking-tight text-zinc-100">
              Competitors
            </h1>
            <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              {competitors.length}
            </span>
          </div>
        </div>

        {flash && (
          <div className="mb-4 px-4 py-2.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-sm text-emerald-300">
            {flash}
          </div>
        )}

        {/* Grid */}
        {competitors.length === 0 ? (
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-10 text-center">
            <MagnifyingGlass size={40} className="text-zinc-700 mx-auto mb-3" />
            <p className="text-zinc-400 text-sm">No competitors discovered yet.</p>
            <p className="text-zinc-600 text-xs mt-1">
              Run the competitor-intel agent to start tracking rivals.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {competitors.map((c) => {
              const findingCount = (c.metadata_json as any)?._finding_count ?? 0
              const depth = depthLabel(findingCount)
              const isDeepening = !!deepening[c.id]
              return (
                <Link
                  key={c.id}
                  href={`/projects/${params.id}/competitors/${c.id}`}
                  className="group bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-xl p-5 transition-colors duration-200"
                >
                  <div className="flex items-start justify-between mb-2">
                    <h2 className="text-base font-semibold text-zinc-100 group-hover:text-emerald-400 transition-colors duration-150">
                      {c.name}
                    </h2>
                    <ArrowRight
                      size={16}
                      className="text-zinc-600 group-hover:text-emerald-400 transition-colors duration-150 mt-1 shrink-0"
                    />
                  </div>
                  {c.description && (
                    <p className="text-sm text-zinc-400 line-clamp-2 mb-3">
                      {c.description}
                    </p>
                  )}
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800 text-zinc-300 border border-zinc-700">
                      {c.entity_type}
                    </span>
                    <span
                      className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium border ${depth.tone}`}
                      title={`Profile depth: ${depth.label}. Each new finding pushes to a higher band. Bands: 0 → 10%, 1-2 → 30%, 3-4 → 60%, 5-7 → 90%, 8+ → 100%.`}
                    >
                      {depth.pct}% · {findingCount} finding{findingCount === 1 ? '' : 's'}
                    </span>
                    {findingCount < 8 && (
                      <button
                        type="button"
                        onClick={(e) => handleDeepen(e, c.id, c.name)}
                        disabled={isDeepening}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 disabled:opacity-50 transition-colors"
                        title="Run LLM probing prompts to extract 8-10 sharp facts about this competitor"
                      >
                        <Sparkle size={11} weight="fill" /> {isDeepening ? 'Queuing…' : 'Deepen'}
                      </button>
                    )}
                    <span className="text-xs text-zinc-600 ml-auto">
                      {new Date(c.last_updated_at).toLocaleDateString()}
                    </span>
                  </div>
                </Link>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
