'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  ArrowLeft,
  ArrowSquareOut,
  Newspaper,
  Lightning,
  CurrencyDollar,
  ChartLineUp,
  Scales,
  Eye,
  LinkSimple,
  Image,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { KnowledgeEntityDetail, KnowledgeScreenshot } from '@/lib/types'

const observationColors: Record<string, string> = {
  news: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  feature_change: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  pricing_update: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  metric: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
  regulatory: 'bg-red-500/10 text-red-400 border-red-500/20',
}

const observationIcons: Record<string, typeof Newspaper> = {
  news: Newspaper,
  feature_change: Lightning,
  pricing_update: CurrencyDollar,
  metric: ChartLineUp,
  regulatory: Scales,
}

function ObservationBadge({ type }: { type: string }) {
  const color = observationColors[type] || 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20'
  const Icon = observationIcons[type] || Eye
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium border ${color}`}>
      <Icon size={12} />
      {type.replace(/_/g, ' ')}
    </span>
  )
}

export default function CompetitorDetailPage({ params }: { params: { id: string; cid: string } }) {
  const entityId = parseInt(params.cid, 10)

  const [entity, setEntity] = useState<KnowledgeEntityDetail | null>(null)
  const [screenshots, setScreenshots] = useState<KnowledgeScreenshot[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      try {
        const [e, ss] = await Promise.all([
          api.getEntity(entityId),
          api.listEntityScreenshots(entityId),
        ])
        setEntity(e)
        setScreenshots(ss)
      } catch (err: any) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [entityId])

  if (loading) {
    return (
      <div className="min-h-screen bg-zinc-950 p-6 md:p-10">
        <div className="max-w-4xl mx-auto">
          <div className="h-4 w-24 bg-zinc-800 rounded animate-pulse mb-6" />
          <div className="h-8 w-64 bg-zinc-800 rounded animate-pulse mb-3" />
          <div className="h-4 w-full bg-zinc-800 rounded animate-pulse mb-2" />
          <div className="h-4 w-3/4 bg-zinc-800 rounded animate-pulse mb-8" />
          <div className="h-40 bg-zinc-900 border border-zinc-800 rounded-xl animate-pulse" />
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

  if (!entity) {
    return (
      <div className="min-h-screen bg-zinc-950 p-6 md:p-10">
        <p className="text-zinc-500 text-sm">Entity not found.</p>
      </div>
    )
  }

  const sortedObservations = [...entity.observations].sort(
    (a, b) => new Date(b.observed_at).getTime() - new Date(a.observed_at).getTime()
  )

  return (
    <div className="min-h-screen bg-zinc-950 p-6 md:p-10">
      <div className="max-w-4xl mx-auto">
        {/* Back link */}
        <Link
          href={`/projects/${params.id}/competitors`}
          className="inline-flex items-center gap-1.5 text-zinc-500 hover:text-zinc-300 text-sm mb-6 transition-colors duration-150"
        >
          <ArrowLeft size={14} />
          All competitors
        </Link>

        {/* Entity header */}
        <div className="mb-8">
          <div className="flex items-start gap-3 mb-3">
            <h1 className="text-2xl font-semibold tracking-tight text-zinc-100">
              {entity.name}
            </h1>
            <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800 text-zinc-300 border border-zinc-700 mt-1">
              {entity.entity_type}
            </span>
            <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 mt-1">
              {(entity.confidence * 100).toFixed(0)}%
            </span>
          </div>
          {entity.description && (
            <p className="text-zinc-400 text-sm max-w-2xl mb-3">{entity.description}</p>
          )}
          {entity.metadata_json && Object.keys(entity.metadata_json).length > 0 && (
            <div className="flex flex-wrap gap-2">
              {Object.entries(entity.metadata_json).map(([k, v]) => (
                <span
                  key={k}
                  className="text-xs text-zinc-500 bg-zinc-900 border border-zinc-800 rounded px-2 py-1"
                >
                  <span className="text-zinc-600">{k}:</span> {String(v)}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Relations */}
        {entity.relations.length > 0 && (
          <section className="mb-8">
            <h2 className="text-lg font-medium text-zinc-100 flex items-center gap-2 mb-4">
              <LinkSimple size={18} className="text-emerald-400" />
              Relations
              <span className="text-xs text-zinc-500 font-normal">({entity.relations.length})</span>
            </h2>
            <div className="space-y-2">
              {entity.relations.map((rel) => (
                <div
                  key={rel.id}
                  className="bg-zinc-900 border border-zinc-800 rounded-lg p-3 flex items-center justify-between gap-3 text-sm"
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-violet-500/10 text-violet-400 border border-violet-500/20">
                      {rel.relation_type.replace(/_/g, ' ')}
                    </span>
                    <span className="text-zinc-400">
                      {rel.from_entity_id === entity.id
                        ? `→ Entity #${rel.to_entity_id}`
                        : `← Entity #${rel.from_entity_id}`}
                    </span>
                  </div>
                  <span className="text-xs text-zinc-600 shrink-0">
                    {new Date(rel.created_at).toLocaleDateString()}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Timeline */}
        {sortedObservations.length > 0 && (
          <section className="mb-8">
            <h2 className="text-lg font-medium text-zinc-100 flex items-center gap-2 mb-4">
              <Eye size={18} className="text-emerald-400" />
              Timeline
              <span className="text-xs text-zinc-500 font-normal">({sortedObservations.length})</span>
            </h2>
            <div className="space-y-3">
              {sortedObservations.map((obs) => (
                <div
                  key={obs.id}
                  className="bg-zinc-900 border border-zinc-800 rounded-xl p-4"
                >
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <ObservationBadge type={obs.observation_type} />
                    <span className="text-xs text-zinc-600 shrink-0">
                      {new Date(obs.observed_at).toLocaleDateString()}
                    </span>
                  </div>
                  <p className="text-sm text-zinc-300 leading-relaxed">{obs.content}</p>
                  {obs.source_url && (
                    <a
                      href={obs.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-emerald-400 hover:text-emerald-300 mt-2 transition-colors duration-150"
                    >
                      <ArrowSquareOut size={12} />
                      Source
                    </a>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Screenshots */}
        {screenshots.length > 0 && (
          <section className="mb-8">
            <h2 className="text-lg font-medium text-zinc-100 flex items-center gap-2 mb-4">
              <Image size={18} className="text-emerald-400" />
              Screenshots
              <span className="text-xs text-zinc-500 font-normal">({screenshots.length})</span>
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              {screenshots.map((ss) => (
                <div
                  key={ss.id}
                  className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden group"
                >
                  <div className="aspect-video bg-zinc-800 relative">
                    <img
                      src={ss.thumbnail_path || ss.file_path}
                      alt={ss.screen_label || `Screenshot ${ss.id}`}
                      className="w-full h-full object-cover"
                    />
                  </div>
                  <div className="p-2.5">
                    {ss.screen_label && (
                      <p className="text-xs font-medium text-zinc-300 truncate">
                        {ss.screen_label}
                      </p>
                    )}
                    <p className="text-xs text-zinc-600 mt-0.5">
                      {new Date(ss.captured_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
