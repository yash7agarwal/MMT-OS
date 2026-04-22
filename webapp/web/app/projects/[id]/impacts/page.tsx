'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  GitBranch,
  Lightning,
  ArrowRight,
  Warning,
  TrendUp,
  Buildings,
  CaretDown,
  CaretUp,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import { ErrorBanner } from '@/components/ErrorBanner'

interface GraphNode {
  id: string
  type: 'trend' | 'effect' | 'company'
  name: string
  description: string
  metadata: Record<string, any>
}

interface GraphEdge {
  from: string
  to: string
  relation: string
  metadata: Record<string, any>
}

const SEVERITY_COLORS: Record<string, string> = {
  high: 'text-red-400 bg-red-500/10 border-red-500/20',
  medium: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  low: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
}

const TIMEFRAME_LABELS: Record<string, string> = {
  near: '< 6 months',
  medium: '6-18 months',
  long: '> 18 months',
}

export default function ImpactsPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [edges, setEdges] = useState<GraphEdge[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedTrend, setExpandedTrend] = useState<string | null>(null)

  useEffect(() => {
    setError(null)
    api.impactGraph(projectId)
      .then((d: any) => { setNodes(d.nodes || []); setEdges(d.edges || []) })
      .catch((err: Error) => setError(err.message || String(err)))
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) {
    return <div className="space-y-4">{[0,1,2].map(i => <div key={i} className="skeleton h-32 w-full rounded-xl" />)}</div>
  }

  if (error) {
    return <ErrorBanner message={error} />
  }

  const trends = nodes.filter(n => n.type === 'trend')
  const effects = nodes.filter(n => n.type === 'effect')
  const companies = nodes.filter(n => n.type === 'company')

  // Build cascade: trend → effects → company impacts
  const cascades = trends.map(trend => {
    const trendEffects = edges
      .filter(e => e.from === trend.id && (e.relation === 'causes' || e.relation === 'leads_to'))
      .map(e => {
        const effect = [...effects, ...companies].find(n => n.id === e.to)
        if (!effect) return null
        const impacts = edges
          .filter(ie => ie.from === effect.id && ie.relation === 'impacts')
          .map(ie => ({ company: companies.find(n => n.id === ie.to)!, edge: ie }))
          .filter(x => x.company)
        return { effect, edge: e, impacts }
      })
      .filter(Boolean) as any[]
    return { trend, effects: trendEffects }
  })

  if (trends.length === 0) {
    return (
      <div className="border border-dashed border-zinc-800 rounded-xl p-12 text-center">
        <GitBranch size={32} className="text-zinc-700 mx-auto mb-3" />
        <h3 className="text-lg font-medium text-zinc-300 mb-2">Impact Analysis</h3>
        <p className="text-sm text-zinc-500 max-w-md mx-auto mb-4">
          Trace how macro trends cascade into 2nd and 3rd order effects on competitors.
          Run the Impact Analysis agent from the Intelligence tab.
        </p>
        <Link href={`/projects/${projectId}/intelligence`} className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          <Lightning size={14} weight="fill" /> Go to Intelligence
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-500">
        How macro trends cascade into effects that impact specific companies. Click a trend to expand.
      </p>

      {cascades.map(({ trend, effects: trendEffects }) => {
        const isExpanded = expandedTrend === trend.id
        const totalImpacts = trendEffects.reduce((s: number, e: any) => s + e.impacts.length, 0)

        return (
          <div key={trend.id} className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
            <button
              onClick={() => setExpandedTrend(isExpanded ? null : trend.id)}
              className="w-full px-4 py-4 flex items-start justify-between gap-3 text-left hover:bg-zinc-800/30 transition-colors"
            >
              <div className="flex items-start gap-3 min-w-0">
                <TrendUp size={20} className="text-emerald-400 mt-0.5 shrink-0" weight="duotone" />
                <div className="min-w-0">
                  <h3 className="font-medium text-zinc-100">{trend.name}</h3>
                  <p className="text-sm text-zinc-500 mt-0.5 line-clamp-2">{trend.description}</p>
                  {trendEffects.length > 0 && (
                    <div className="flex items-center gap-3 mt-2 text-xs text-zinc-600">
                      <span>{trendEffects.length} effect{trendEffects.length !== 1 ? 's' : ''}</span>
                      <span>{totalImpacts} company impact{totalImpacts !== 1 ? 's' : ''}</span>
                    </div>
                  )}
                </div>
              </div>
              {trendEffects.length > 0 && (
                isExpanded ? <CaretUp size={16} className="text-zinc-500 mt-1" /> : <CaretDown size={16} className="text-zinc-500 mt-1" />
              )}
            </button>

            {isExpanded && trendEffects.length > 0 && (
              <div className="border-t border-zinc-800/60">
                {trendEffects.map(({ effect, impacts }: any) => (
                  <div key={effect.id} className="border-b border-zinc-800/30 last:border-0">
                    <div className="px-4 py-3 flex items-start gap-3 bg-zinc-950/30">
                      <div className="flex items-center gap-1.5 shrink-0 mt-0.5">
                        <ArrowRight size={12} className="text-zinc-600" />
                        <span className="text-xs text-amber-400 font-medium">2nd order</span>
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-zinc-300">{effect.name}</p>
                        {effect.description && effect.description !== effect.name && (
                          <p className="text-xs text-zinc-500 mt-0.5 line-clamp-2">{effect.description}</p>
                        )}
                        {effect.metadata?.severity && (
                          <div className="flex items-center gap-2 mt-1.5">
                            <span className={`text-xs px-1.5 py-0.5 rounded border ${SEVERITY_COLORS[effect.metadata.severity] || SEVERITY_COLORS.medium}`}>
                              {effect.metadata.severity}
                            </span>
                            {effect.metadata?.timeframe && (
                              <span className="text-xs text-zinc-600">{TIMEFRAME_LABELS[effect.metadata.timeframe] || effect.metadata.timeframe}</span>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                    {impacts.length > 0 && (
                      <div className="pl-12 pr-4 py-2 space-y-1 bg-zinc-950/50">
                        {impacts.map(({ company, edge: ie }: any) => (
                          <div key={company.id} className="flex items-center gap-2 py-1">
                            <ArrowRight size={10} className="text-zinc-700" />
                            <span className="text-xs text-zinc-600">3rd</span>
                            {ie.metadata?.is_threat
                              ? <Warning size={12} className="text-red-400" />
                              : <TrendUp size={12} className="text-emerald-400" />}
                            <Buildings size={12} className="text-zinc-500" />
                            <span className="text-sm text-zinc-300">{company.name}</span>
                            {ie.metadata?.severity && (
                              <span className={`text-xs px-1.5 py-0.5 rounded border ${SEVERITY_COLORS[ie.metadata.severity] || SEVERITY_COLORS.medium}`}>
                                {ie.metadata.severity}
                              </span>
                            )}
                            <span className="text-xs text-zinc-600 ml-auto">{ie.metadata?.is_threat ? 'threat' : 'opportunity'}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {isExpanded && trendEffects.length === 0 && (
              <div className="px-4 py-3 border-t border-zinc-800/60 text-xs text-zinc-600">
                No cascade effects analyzed yet. Run the Impact Analysis agent.
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
