'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  ArrowLeft,
  Palette,
  TrendUp,
  Package,
  CurrencyDollar,
  Cpu,
  ShieldCheck,
  CastleTurret,
  Compass,
  ArrowSquareOut,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import { ErrorBanner } from '@/components/ErrorBanner'

const LENS_META: Record<string, { icon: typeof Palette; label: string; question: string }> = {
  product_craft: { icon: Palette, label: 'Product Craft', question: 'How good is their product execution?' },
  growth: { icon: TrendUp, label: 'Growth', question: 'How are they growing?' },
  supply: { icon: Package, label: 'Supply', question: 'What supply advantage do they have?' },
  monetization: { icon: CurrencyDollar, label: 'Monetization', question: 'How do they make money?' },
  technology: { icon: Cpu, label: 'Technology', question: "What's their tech edge?" },
  brand_trust: { icon: ShieldCheck, label: 'Brand & Trust', question: 'How strong is their brand?' },
  moat: { icon: CastleTurret, label: 'Moat', question: "What's defensible?" },
  trajectory: { icon: Compass, label: 'Trajectory', question: 'Where are they headed?' },
}

type Observation = {
  id: number
  observation_type: string
  content: string
  source_url?: string
  created_at: string
}

type LensEntity = {
  id: number
  name: string
  entity_type: string
  observations: Observation[]
}

type LensDetailData = {
  lens: string
  entities: LensEntity[]
}

function renderInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = []
  const regex = /\*\*([^*]+)\*\*/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index))
    parts.push(
      <strong key={match.index} className="font-semibold text-zinc-100">
        {match[1]}
      </strong>
    )
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex))
  return parts.length > 0 ? parts : text
}

function typeBadgeColor(type: string): string {
  switch (type) {
    case 'strength': return 'bg-emerald-500/10 text-emerald-400'
    case 'weakness': return 'bg-red-500/10 text-red-400'
    case 'opportunity': return 'bg-amber-500/10 text-amber-400'
    case 'threat': return 'bg-rose-500/10 text-rose-400'
    default: return 'bg-zinc-800 text-zinc-400'
  }
}

export default function LensDetailPage({
  params,
}: {
  params: { id: string; lens: string }
}) {
  const projectId = parseInt(params.id, 10)
  const lensName = params.lens
  const [data, setData] = useState<LensDetailData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})

  const meta = LENS_META[lensName]
  const Icon = meta?.icon ?? Compass

  useEffect(() => {
    setError(null)
    api
      .lensDetail(projectId, lensName)
      .then(setData)
      .catch((err: Error) => setError(err.message || String(err)))
      .finally(() => setLoading(false))
  }, [projectId, lensName])

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-5 w-32" />
        <div className="skeleton h-8 w-64" />
        <div className="skeleton h-48 w-full rounded-xl" />
      </div>
    )
  }

  return (
    <div>
      <Link
        href={`/projects/${projectId}/lenses`}
        className="inline-flex items-center gap-1.5 text-zinc-500 hover:text-zinc-300 text-sm mb-4 transition-colors duration-150"
      >
        <ArrowLeft size={14} />
        All Lenses
      </Link>

      <div className="flex items-center gap-3 mb-1">
        <Icon size={24} className="text-emerald-400" />
        <h2 className="text-xl font-semibold tracking-tight">
          {meta?.label ?? lensName}
        </h2>
      </div>
      {meta && (
        <p className="text-zinc-500 text-sm mb-6 ml-9">{meta.question}</p>
      )}

      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      {!data || data.entities.length === 0 ? (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-12 text-center">
          <p className="text-zinc-400 text-sm">
            No observations found for this lens.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {data.entities.map((entity) => (
            <div
              key={entity.id}
              className="rounded-xl border border-zinc-800 bg-zinc-900 overflow-hidden"
            >
              <div className="px-5 py-4 border-b border-zinc-800/50 flex items-center justify-between">
                <h3 className="text-zinc-200 font-medium">{entity.name}</h3>
                <span className="text-xs text-zinc-500">
                  {entity.observations.length} observation
                  {entity.observations.length !== 1 ? 's' : ''}
                </span>
              </div>

              <div className="divide-y divide-zinc-800/50">
                {entity.observations.map((obs) => {
                  const isLong = obs.content.length > 200
                  const isExpanded = expanded[obs.id]
                  const displayText =
                    isLong && !isExpanded
                      ? obs.content.slice(0, 200) + '...'
                      : obs.content

                  return (
                    <div key={obs.id} className="px-5 py-3.5">
                      <div className="flex items-center gap-2 mb-1.5">
                        <span
                          className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${typeBadgeColor(obs.observation_type)}`}
                        >
                          {obs.observation_type}
                        </span>
                        <span className="text-zinc-600 text-xs">
                          {new Date(obs.created_at).toLocaleDateString()}
                        </span>
                      </div>
                      <p className="text-zinc-300 text-sm leading-relaxed">
                        {renderInline(displayText)}
                      </p>
                      {isLong && (
                        <button
                          onClick={() =>
                            setExpanded((prev) => ({
                              ...prev,
                              [obs.id]: !prev[obs.id],
                            }))
                          }
                          className="text-emerald-400 text-xs mt-1 hover:text-emerald-300 transition-colors"
                        >
                          {isExpanded ? 'Show less' : 'Read more'}
                        </button>
                      )}
                      {obs.source_url && (
                        <a
                          href={obs.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300 mt-1.5 transition-colors"
                        >
                          <ArrowSquareOut size={12} />
                          Source
                        </a>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
