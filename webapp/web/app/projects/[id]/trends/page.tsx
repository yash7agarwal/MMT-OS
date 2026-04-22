'use client'

import { useEffect, useState } from 'react'
import {
  TrendUp,
  ArrowSquareOut,
  Users,
  Cpu,
  Scales,
  Buildings,
  Globe,
  CaretDown,
  CaretUp,
  ThumbsUp,
  X as XIcon,
  Star,
  Trash,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import { ErrorBanner } from '@/components/ErrorBanner'

interface Observation {
  id: number
  type: string
  content: string
  source_url: string
  recorded_at: string
  lens_tags: string[]
}

interface Adoption {
  company_id: number
  company_name: string
  adoption_level: 'strong' | 'emerging' | 'absent' | 'unknown'
}

interface Trend {
  id: number
  name: string
  description: string
  timeline: 'past' | 'present' | 'emerging' | 'future'
  category: string
  confidence?: number
  user_signal?: 'kept' | 'dismissed' | 'starred' | null
  dismissed_reason?: string | null
  quantification: Record<string, string>
  observations: Observation[]
  adoption: Adoption[]
  observation_count: number
}

type Timeline = 'past' | 'present' | 'emerging' | 'future'

const TIMELINES: { key: Timeline; label: string; accent: string; bg: string; border: string; dot: string }[] = [
  { key: 'past', label: 'Past', accent: 'text-zinc-400', bg: 'bg-zinc-600/10', border: 'border-zinc-600/30', dot: 'bg-zinc-600' },
  { key: 'present', label: 'Present', accent: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/20', dot: 'bg-emerald-400' },
  { key: 'emerging', label: 'Emerging', accent: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/20', dot: 'bg-amber-400' },
  { key: 'future', label: 'Future', accent: 'text-cyan-400', bg: 'bg-cyan-500/10', border: 'border-cyan-500/20', dot: 'bg-cyan-400' },
]

const CATEGORIES: { key: string; label: string; color: string; Icon: typeof Cpu }[] = [
  { key: 'technology', label: 'Technology', color: 'bg-cyan-500/20 text-cyan-400', Icon: Cpu },
  { key: 'consumer_behavior', label: 'Consumer Behavior', color: 'bg-amber-500/20 text-amber-400', Icon: Users },
  { key: 'regulation', label: 'Regulation', color: 'bg-red-500/20 text-red-400', Icon: Scales },
  { key: 'demographics', label: 'Demographics', color: 'bg-cyan-500/20 text-cyan-400', Icon: Users },
  { key: 'market_structure', label: 'Market Structure', color: 'bg-emerald-500/20 text-emerald-400', Icon: Buildings },
  { key: 'general', label: 'General', color: 'bg-zinc-500/20 text-zinc-400', Icon: Globe },
]

function categoryBadge(cat: string) {
  const found = CATEGORIES.find(c => c.key === cat)
  const color = found?.color ?? 'bg-zinc-500/20 text-zinc-400'
  const label = found?.label ?? cat
  return <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${color}`}>{label}</span>
}

function adoptionIcon(level: string) {
  if (level === 'strong') return <span className="inline-block w-2.5 h-2.5 rounded-full bg-emerald-400" />
  if (level === 'emerging') return <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-amber-400 bg-amber-400/40" />
  if (level === 'absent') return <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-red-400 bg-transparent" />
  return <span className="inline-block w-2.5 h-2.5 rounded-full bg-zinc-600" />
}

function TrendCard({ trend, onRemove, onUpdate }: {
  trend: Trend
  onRemove: (id: number) => void
  onUpdate: (id: number, patch: Partial<Trend>) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [busy, setBusy] = useState<null | 'keep' | 'dismiss' | 'star' | 'purge'>(null)
  const quantKeys = Object.keys(trend.quantification || {})

  async function sendSignal(signal: 'kept' | 'dismissed' | 'starred') {
    const key = signal === 'kept' ? 'keep' : signal === 'dismissed' ? 'dismiss' : 'star'
    setBusy(key)
    try {
      await api.setEntitySignal(trend.id, signal)
      if (signal === 'dismissed') {
        onRemove(trend.id)   // matches backend filter — dismissed hidden from view
      } else {
        onUpdate(trend.id, { user_signal: signal })
      }
    } catch (_err) {
      setBusy(null)
    }
  }

  async function purge() {
    if (!confirm(`Purge "${trend.name}"? This cascade-deletes its observations and queues a fresh research run.`)) return
    setBusy('purge')
    try {
      await api.purgeEntity(trend.id)
      onRemove(trend.id)
    } catch (_err) {
      setBusy(null)
    }
  }

  const starred = trend.user_signal === 'starred'
  const kept = trend.user_signal === 'kept'

  return (
    <div className={`bg-zinc-900/80 border rounded-xl p-4 transition-colors min-w-[320px] max-w-[400px] flex-shrink-0 ${
      starred ? 'border-amber-500/60' : 'border-zinc-800 hover:border-zinc-700'
    }`}>
      <div className="flex items-start justify-between gap-2 mb-2">
        {categoryBadge(trend.category)}
        <span className="text-[11px] text-zinc-500 font-mono">{trend.observation_count} obs</span>
      </div>

      <h3 className="text-sm font-semibold text-zinc-100 mb-1 leading-snug">{trend.name}</h3>
      <p className="text-xs text-zinc-500 line-clamp-2 leading-relaxed mb-3">{trend.description}</p>

      {quantKeys.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mb-3">
          {quantKeys.map(k => (
            <div key={k} className="text-[11px]">
              <span className="text-zinc-600">{k.replace(/_/g, ' ')}:</span>{' '}
              <span className="text-zinc-300 font-medium">{trend.quantification[k]}</span>
            </div>
          ))}
        </div>
      )}

      {trend.adoption.length > 0 && (
        <div className="space-y-1 mb-3">
          <span className="text-[10px] text-zinc-600 uppercase tracking-wider">Competitor Adoption</span>
          {trend.adoption.map(a => (
            <div key={a.company_id} className="flex items-center gap-2 text-xs">
              {adoptionIcon(a.adoption_level)}
              <span className="text-zinc-400">{a.company_name}</span>
              <span className="text-zinc-600">({a.adoption_level})</span>
            </div>
          ))}
        </div>
      )}

      {trend.observations.length > 0 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-300 transition-colors mt-1"
        >
          {expanded ? <CaretUp size={12} /> : <CaretDown size={12} />}
          {expanded ? 'Hide' : 'Show'} findings ({trend.observations.length})
        </button>
      )}

      {expanded && (
        <div className="mt-3 space-y-2 border-t border-zinc-800 pt-3">
          {trend.observations.map(obs => (
            <div key={obs.id} className="text-xs text-zinc-400 leading-relaxed">
              <p>{obs.content}</p>
              {obs.source_url && (
                <a
                  href={obs.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-emerald-500 hover:text-emerald-400 mt-1"
                >
                  <ArrowSquareOut size={10} /> Source
                </a>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Feedback row — same signals as Telegram digest. Starred highlights the
          card border; Dismiss removes it from the view and lets the planner
          avoid this pattern next run. Purge is destructive-plus-rerun. */}
      <div className="mt-3 border-t border-zinc-800 pt-3 flex items-center gap-1.5">
        <button
          onClick={() => sendSignal('kept')}
          disabled={busy !== null}
          title="Keep — marks this as useful, weights similar patterns up"
          className={`flex items-center gap-1 text-[11px] px-2 py-1 rounded-md transition-colors ${
            kept ? 'bg-emerald-500/20 text-emerald-300' : 'text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300'
          } disabled:opacity-40`}
        >
          <ThumbsUp size={12} weight={kept ? 'fill' : 'regular'} />
          {busy === 'keep' ? '…' : 'Keep'}
        </button>
        <button
          onClick={() => sendSignal('dismissed')}
          disabled={busy !== null}
          title="Dismiss — hides this and feeds it back as a negative example"
          className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-md text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300 transition-colors disabled:opacity-40"
        >
          <XIcon size={12} />
          {busy === 'dismiss' ? '…' : 'Dismiss'}
        </button>
        <button
          onClick={() => sendSignal('starred')}
          disabled={busy !== null}
          title="Star — prioritises this pattern in the next research brief"
          className={`flex items-center gap-1 text-[11px] px-2 py-1 rounded-md transition-colors ${
            starred ? 'bg-amber-500/20 text-amber-300' : 'text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300'
          } disabled:opacity-40`}
        >
          <Star size={12} weight={starred ? 'fill' : 'regular'} />
          {busy === 'star' ? '…' : 'Star'}
        </button>
        <button
          onClick={purge}
          disabled={busy !== null}
          title="Purge & re-research — for mis-tagged entries. Deletes observations, queues a fresh run, blocks re-learning."
          className="ml-auto flex items-center gap-1 text-[11px] px-2 py-1 rounded-md text-zinc-600 hover:bg-red-500/10 hover:text-red-400 transition-colors disabled:opacity-40"
        >
          <Trash size={12} />
          {busy === 'purge' ? '…' : 'Purge'}
        </button>
      </div>
    </div>
  )
}

export default function TrendsPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [trends, setTrends] = useState<Trend[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('all')

  useEffect(() => {
    let cancelled = false
    setError(null)
    api.trendsView(projectId).then((data) => {
      if (!cancelled) setTrends(data.trends || [])
    }).catch((err: Error) => {
      if (!cancelled) setError(err.message || String(err))
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [projectId])

  const removeTrend = (id: number) => setTrends(curr => curr.filter(t => t.id !== id))
  const patchTrend = (id: number, patch: Partial<Trend>) =>
    setTrends(curr => curr.map(t => (t.id === id ? { ...t, ...patch } : t)))

  const filtered = filter === 'all' ? trends : trends.filter(t => t.category === filter)

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[400px]">
        <div className="text-zinc-600 text-sm">Loading trends...</div>
      </div>
    )
  }

  if (error) {
    return <ErrorBanner message={error} />
  }

  if (trends.length === 0) {
    return (
      <div className="text-center py-16">
        <TrendUp size={32} className="text-zinc-700 mx-auto mb-3" />
        <h3 className="text-lg font-medium text-zinc-300 mb-2">Industry Trends</h3>
        <p className="text-sm text-zinc-500 max-w-md mx-auto">
          No industry trends discovered yet. Run the Industry Research agent from the Intelligence tab to start tracking trends.
        </p>
      </div>
    )
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-xl font-semibold text-zinc-100 flex items-center gap-2">
          <TrendUp size={22} weight="duotone" className="text-emerald-400" />
          Industry Trends
        </h2>
        <p className="text-sm text-zinc-500 mt-1">Past, present, and future forces shaping your market</p>
      </div>

      {/* Filter bar */}
      <div className="flex gap-1.5 mb-6 flex-wrap">
        <button
          onClick={() => setFilter('all')}
          className={`text-xs px-3 py-1.5 rounded-lg font-medium transition-colors ${
            filter === 'all' ? 'bg-zinc-700 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
          }`}
        >
          All
        </button>
        {CATEGORIES.map(cat => (
          <button
            key={cat.key}
            onClick={() => setFilter(cat.key)}
            className={`text-xs px-3 py-1.5 rounded-lg font-medium transition-colors flex items-center gap-1.5 ${
              filter === cat.key ? 'bg-zinc-700 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
            }`}
          >
            <cat.Icon size={12} />
            {cat.label}
          </button>
        ))}
      </div>

      {/* Timeline flow */}
      <div className="relative space-y-8">
        {/* Connecting line */}
        <div className="absolute left-6 top-4 bottom-4 w-px bg-gradient-to-b from-zinc-600 via-emerald-500/40 via-amber-500/40 to-cyan-500/40" />

        {TIMELINES.map(tl => {
          const sectionTrends = filtered.filter(t => t.timeline === tl.key)
          if (sectionTrends.length === 0 && filter !== 'all') return null

          return (
            <div key={tl.key} className="relative pl-14">
              {/* Timeline dot */}
              <div className={`absolute left-[18px] top-2 w-3.5 h-3.5 rounded-full ${tl.dot} ring-4 ring-zinc-950 z-10`} />

              {/* Section label */}
              <div className="mb-3">
                <h3 className={`text-sm font-semibold uppercase tracking-wider ${tl.accent}`}>{tl.label}</h3>
              </div>

              {sectionTrends.length === 0 ? (
                <p className="text-xs text-zinc-600 italic">No trends in this phase</p>
              ) : (
                <div className="flex gap-4 overflow-x-auto pb-2 -mx-2 px-2 scrollbar-thin scrollbar-thumb-zinc-800">
                  {sectionTrends.map(trend => (
                    <TrendCard
                      key={trend.id}
                      trend={trend}
                      onRemove={removeTrend}
                      onUpdate={patchTrend}
                    />
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Legend */}
      <div className="mt-8 flex flex-wrap gap-4 text-[11px] text-zinc-600 border-t border-zinc-800/50 pt-4">
        <span className="flex items-center gap-1.5">{adoptionIcon('strong')} Strong adoption</span>
        <span className="flex items-center gap-1.5">{adoptionIcon('emerging')} Emerging adoption</span>
        <span className="flex items-center gap-1.5">{adoptionIcon('absent')} Absent</span>
        <span className="flex items-center gap-1.5">{adoptionIcon('unknown')} Unknown</span>
      </div>
    </div>
  )
}
