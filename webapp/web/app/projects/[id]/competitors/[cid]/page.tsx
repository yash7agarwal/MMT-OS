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
  Lightbulb,
  Sparkle,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { KnowledgeEntityDetail, KnowledgeScreenshot, KnowledgeObservation, KnowledgeArtifact } from '@/lib/types'

/* ------------------------------------------------------------------ */
/* Observation type metadata                                           */
/* ------------------------------------------------------------------ */
const OBS_META: Record<string, { icon: typeof Newspaper; color: string; bg: string; label: string }> = {
  news:           { icon: Newspaper,     color: 'text-blue-400',    bg: 'bg-blue-500/10 border-blue-500/20',    label: 'Recent Moves' },
  feature_change: { icon: Lightning,     color: 'text-emerald-400', bg: 'bg-emerald-500/10 border-emerald-500/20', label: 'Feature Change' },
  pricing_update: { icon: CurrencyDollar,color: 'text-amber-400',   bg: 'bg-amber-500/10 border-amber-500/20',   label: 'Pricing' },
  metric:         { icon: ChartLineUp,   color: 'text-cyan-400',    bg: 'bg-cyan-500/10 border-cyan-500/20',    label: 'Metrics' },
  regulatory:     { icon: Scales,        color: 'text-red-400',     bg: 'bg-red-500/10 border-red-500/20',      label: 'Regulatory' },
  general:        { icon: Lightbulb,     color: 'text-zinc-400',    bg: 'bg-zinc-500/10 border-zinc-500/20',    label: 'Finding' },
}

/* ------------------------------------------------------------------ */
/* Markdown-lite renderer                                              */
/* Converts **bold**, bullet lists, numbered lists into React elements */
/* ------------------------------------------------------------------ */
function RenderContent({ text }: { text: string }) {
  // Split into paragraphs (double newline or single newline)
  const blocks = text.split(/\n{2,}/).filter(Boolean)

  return (
    <div className="space-y-3">
      {blocks.map((block, bi) => {
        const trimmed = block.trim()

        // Check if it's a list (lines starting with - or * or (1) or 1.)
        const lines = trimmed.split('\n').map(l => l.trim()).filter(Boolean)
        const isList = lines.every(l => /^[-*•]|\(\d+\)|^\d+[.)]\s/.test(l))

        if (isList) {
          return (
            <ul key={bi} className="space-y-1.5 pl-1">
              {lines.map((line, li) => {
                const cleaned = line.replace(/^[-*•]\s*|\(\d+\)\s*|^\d+[.)]\s*/, '')
                return (
                  <li key={li} className="flex gap-2 text-sm text-zinc-300 leading-relaxed">
                    <span className="text-zinc-600 mt-1 shrink-0">-</span>
                    <span>{renderInline(cleaned)}</span>
                  </li>
                )
              })}
            </ul>
          )
        }

        // Check if it looks like a header (starts with ** and is short)
        if (/^\*\*[^*]+\*\*[:\s—-]*$/.test(trimmed) && trimmed.length < 120) {
          const headerText = trimmed.replace(/\*\*/g, '').replace(/[:\s—-]+$/, '')
          return (
            <h4 key={bi} className="text-sm font-semibold text-zinc-200 mt-2">
              {headerText}
            </h4>
          )
        }

        // Regular paragraph
        return (
          <p key={bi} className="text-sm text-zinc-300 leading-relaxed">
            {renderInline(trimmed)}
          </p>
        )
      })}
    </div>
  )
}

/** Render inline markdown: **bold**, `code`, and plain text */
function renderInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = []
  // Match **bold** or `code`
  const regex = /\*\*([^*]+)\*\*|`([^`]+)`/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(text)) !== null) {
    // Text before match
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index))
    }
    if (match[1]) {
      // Bold
      parts.push(<strong key={match.index} className="font-semibold text-zinc-100">{match[1]}</strong>)
    } else if (match[2]) {
      // Code
      parts.push(<code key={match.index} className="text-xs bg-zinc-800 text-zinc-300 px-1 py-0.5 rounded font-mono">{match[2]}</code>)
    }
    lastIndex = match.index + match[0].length
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }

  return parts.length > 0 ? parts : text
}

/* ------------------------------------------------------------------ */
/* Observation card                                                    */
/* ------------------------------------------------------------------ */
function ObservationCard({ obs }: { obs: KnowledgeObservation }) {
  const meta = OBS_META[obs.observation_type] || OBS_META.general
  const Icon = meta.icon
  const [expanded, setExpanded] = useState(false)
  const isLong = obs.content.length > 300

  // Extract a title from the content (first **bold** phrase or first sentence)
  const titleMatch = obs.content.match(/^\*\*([^*]+)\*\*/)
  const title = titleMatch
    ? titleMatch[1].replace(/[:\s—-]+$/, '')
    : obs.content.split(/[.!?\n]/)[0].slice(0, 80)

  // Content after the title
  const body = titleMatch
    ? obs.content.slice(titleMatch[0].length).trim()
    : obs.content

  const displayBody = !isLong || expanded ? body : body.slice(0, 280)

  return (
    <div className={`border rounded-xl overflow-hidden ${meta.bg}`}>
      {/* Header */}
      <div className="px-4 py-3 flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5 min-w-0">
          <Icon size={16} className={`${meta.color} mt-0.5 shrink-0`} weight="duotone" />
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-0.5">
              <span className={`text-xs font-medium ${meta.color}`}>{meta.label}</span>
              <span className="text-xs text-zinc-600">
                {new Date(obs.observed_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
              </span>
            </div>
            <h4 className="text-sm font-medium text-zinc-200 leading-snug">{title}</h4>
          </div>
        </div>
        {obs.source_url && (
          <a
            href={obs.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-emerald-400 shrink-0 transition-colors"
          >
            <ArrowSquareOut size={12} />
            {(() => { try { return new URL(obs.source_url).hostname.replace('www.', '') } catch { return 'source' } })()}
          </a>
        )}
      </div>

      {/* Body */}
      {body && (
        <div className="px-4 pb-3 pl-9">
          <RenderContent text={displayBody} />
          {isLong && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-xs text-emerald-400 hover:text-emerald-300 mt-2 transition-colors"
            >
              {expanded ? 'Show less' : 'Read more'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Main page                                                           */
/* ------------------------------------------------------------------ */
export default function CompetitorDetailPage({ params }: { params: { id: string; cid: string } }) {
  const entityId = parseInt(params.cid, 10)

  const [entity, setEntity] = useState<KnowledgeEntityDetail | null>(null)
  const [screenshots, setScreenshots] = useState<KnowledgeScreenshot[]>([])
  const [artifacts, setArtifacts] = useState<KnowledgeArtifact[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showReport, setShowReport] = useState<number | null>(null)
  const [deepening, setDeepening] = useState(false)
  const [flash, setFlash] = useState<string | null>(null)

  const handleDeepen = async () => {
    if (!entity) return
    setDeepening(true)
    try {
      const r = await api.deepenCompetitor(entity.id)
      setFlash(r.created
        ? `Queued deep profile for ${entity.name}. The intel agent will pick it up — refresh in 1–2 min.`
        : `Already pending: ${r.reason || 'in queue'}.`)
    } catch (e: any) {
      setFlash(`Failed: ${e.message || e}`)
    } finally {
      setDeepening(false)
      setTimeout(() => setFlash(null), 8000)
    }
  }

  const projectId = parseInt(params.id, 10)

  useEffect(() => {
    Promise.all([
      api.getEntity(entityId),
      api.listEntityScreenshots(entityId),
      api.listArtifacts(projectId, 'competitor_profile'),
    ]).then(([e, ss, arts]) => {
      setEntity(e)
      setScreenshots(ss)
      // Filter artifacts that mention this entity
      const relevant = (arts as KnowledgeArtifact[]).filter(a =>
        a.entity_ids_json?.includes(entityId) ||
        (e as KnowledgeEntityDetail).name && a.title.toLowerCase().includes((e as KnowledgeEntityDetail).name.toLowerCase().split(' ')[0])
      )
      setArtifacts(relevant)
    }).catch((err: any) => setError(err.message))
      .finally(() => setLoading(false))
  }, [entityId, projectId])

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-8 w-64 mb-2" />
        <div className="skeleton h-4 w-full mb-6" />
        {[0,1,2].map(i => <div key={i} className="skeleton h-32 w-full rounded-xl" />)}
      </div>
    )
  }

  if (error || !entity) {
    return <p className="text-red-400 text-sm">{error || 'Entity not found.'}</p>
  }

  // Group observations by type for organized display
  const obsByType = entity.observations.reduce((acc, obs) => {
    const type = obs.observation_type
    if (!acc[type]) acc[type] = []
    acc[type].push(obs)
    return acc
  }, {} as Record<string, KnowledgeObservation[]>)

  // Order: news first, then feature_change, pricing, metric, regulatory, general
  const typeOrder = ['news', 'feature_change', 'pricing_update', 'metric', 'regulatory', 'general']
  const orderedTypes = typeOrder.filter(t => obsByType[t])
  // Add any types not in the predefined order
  Object.keys(obsByType).forEach(t => { if (!orderedTypes.includes(t)) orderedTypes.push(t) })

  // v0.20.2: relabel "confidence" → "Profile depth · X findings". The score
  // really tracks observation count, not confidence; calling it confidence
  // confused users into thinking 30% meant "low quality" when it means
  // "1-2 findings — needs more research."
  const findingCount = entity.observations.length
  const depthBand = findingCount === 0 ? { pct: 10, label: 'No findings yet', tone: 'text-zinc-400 bg-zinc-500/10 border-zinc-500/20' }
    : findingCount <= 2 ? { pct: 30, label: 'Shallow profile', tone: 'text-amber-400 bg-amber-500/10 border-amber-500/20' }
    : findingCount <= 4 ? { pct: 60, label: 'Medium profile', tone: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/20' }
    : findingCount <= 7 ? { pct: 90, label: 'Deep profile', tone: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20' }
    : { pct: 100, label: 'Comprehensive', tone: 'text-emerald-300 bg-emerald-500/15 border-emerald-500/30' }

  return (
    <div>
      {flash && (
        <div className="mb-4 px-4 py-2.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-sm text-emerald-300">
          {flash}
        </div>
      )}
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-2 flex-wrap">
          <h2 className="text-xl font-semibold tracking-tight text-zinc-100">{entity.name}</h2>
          <span
            className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${depthBand.tone}`}
            title={`Profile depth: ${findingCount} findings → ${depthBand.pct}%. Bands: 0=10%, 1-2=30%, 3-4=60%, 5-7=90%, 8+=100%.`}
          >
            {depthBand.pct}% · {findingCount} finding{findingCount === 1 ? '' : 's'} · {depthBand.label}
          </span>
          {findingCount < 8 && (
            <button
              onClick={handleDeepen}
              disabled={deepening}
              className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-medium bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 disabled:opacity-50 transition-colors"
              title="Run LLM probing prompts to extract 8-10 sharp facts (recent moves, pricing, moat, weaknesses, regulatory). Free Groq calls — takes 30-60s once the agent picks it up."
            >
              <Sparkle size={12} weight="fill" />
              {deepening ? 'Queuing…' : 'Deepen profile'}
            </button>
          )}
        </div>
        {entity.description && (
          <p className="text-sm text-zinc-400 max-w-2xl leading-relaxed">{entity.description}</p>
        )}
        {entity.metadata_json && Object.keys(entity.metadata_json).length > 0 && (
          <div className="flex flex-wrap gap-2 mt-3">
            {Object.entries(entity.metadata_json).map(([k, v]) => (
              <span key={k} className="text-xs text-zinc-500 bg-zinc-900 border border-zinc-800 rounded px-2 py-1">
                <span className="text-zinc-600">{k}:</span> {String(v)}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Quick stats bar */}
      <div className="flex items-center gap-4 mb-6 text-xs text-zinc-500">
        <span>{entity.observations.length} findings</span>
        <span className="text-zinc-700">|</span>
        <span>{entity.relations.length} relations</span>
        <span className="text-zinc-700">|</span>
        <span>{screenshots.length} screenshots</span>
        {entity.observations.length > 0 && (
          <>
            <span className="text-zinc-700">|</span>
            <span>Last updated {new Date(entity.observations[0].observed_at).toLocaleDateString()}</span>
          </>
        )}
      </div>

      {/* Full Report (if exists) */}
      {artifacts.length > 0 && (
        <div className="mb-8">
          <h3 className="text-xs font-medium text-emerald-400 uppercase tracking-wider mb-3">
            Full Report ({artifacts.length})
          </h3>
          {artifacts.map(art => (
            <div key={art.id} className="border border-emerald-500/20 bg-emerald-500/5 rounded-xl overflow-hidden">
              <button
                onClick={() => setShowReport(showReport === art.id ? null : art.id)}
                className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-emerald-500/10 transition-colors"
              >
                <div>
                  <h4 className="text-sm font-medium text-zinc-200">{art.title}</h4>
                  <p className="text-xs text-zinc-500 mt-0.5">
                    Generated {new Date(art.generated_at).toLocaleDateString()} by {art.generated_by_agent || 'agent'}
                  </p>
                </div>
                <span className="text-xs text-emerald-400">{showReport === art.id ? 'Collapse' : 'Read report'}</span>
              </button>
              {showReport === art.id && (
                <div className="px-4 pb-4 border-t border-emerald-500/10">
                  <div className="mt-3">
                    <RenderContent text={art.content_md} />
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Findings grouped by type */}
      {orderedTypes.length > 0 ? (
        <div className="space-y-6">
          {orderedTypes.map(type => {
            const observations = obsByType[type]
            const meta = OBS_META[type] || OBS_META.general
            return (
              <section key={type}>
                <h3 className={`text-xs font-medium uppercase tracking-wider mb-3 ${meta.color}`}>
                  {meta.label} ({observations.length})
                </h3>
                <div className="space-y-3">
                  {observations
                    .sort((a, b) => new Date(b.observed_at).getTime() - new Date(a.observed_at).getTime())
                    .map(obs => <ObservationCard key={obs.id} obs={obs} />)
                  }
                </div>
              </section>
            )
          })}
        </div>
      ) : (
        <div className="border border-dashed border-zinc-800 rounded-xl p-8 text-center">
          <Eye size={24} className="text-zinc-700 mx-auto mb-2" />
          <p className="text-sm text-zinc-500">No findings yet. Run the competitive intel agent to research this competitor.</p>
        </div>
      )}

      {/* Relations */}
      {entity.relations.length > 0 && (
        <section className="mt-8">
          <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-3">
            Relations ({entity.relations.length})
          </h3>
          <div className="flex flex-wrap gap-2">
            {entity.relations.map((rel) => (
              <span key={rel.id} className="text-xs bg-zinc-900 border border-zinc-800 text-zinc-400 rounded-lg px-3 py-1.5">
                {rel.relation_type.replace(/_/g, ' ')} {rel.from_entity_id === entity.id ? `→ #${rel.to_entity_id}` : `← #${rel.from_entity_id}`}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* Screenshots */}
      {screenshots.length > 0 && (
        <section className="mt-8">
          <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-3">
            Screenshots ({screenshots.length})
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {screenshots.map((ss) => (
              <div key={ss.id} className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
                <div className="aspect-video bg-zinc-800">
                  <img src={ss.thumbnail_path || ss.file_path} alt={ss.screen_label || ''} className="w-full h-full object-cover" />
                </div>
                {ss.screen_label && <p className="text-xs text-zinc-400 p-2 truncate">{ss.screen_label}</p>}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
