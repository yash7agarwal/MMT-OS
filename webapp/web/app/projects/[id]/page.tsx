'use client'

import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import {
  DeviceMobile,
  ChatCircle,
  Binoculars,
  Play,
  Brain,
  ArrowRight,
  Monitor,
  Image,
  GitBranch,
  ListChecks,
  Lightbulb,
  FileText,
  Clock,
  Newspaper,
  TrendUp,
  CurrencyDollar,
  Wrench,
  Scales,
  ArrowSquareOut,
  Warning,
  CircleNotch,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type {
  ProjectDetail,
  KnowledgeSummary,
} from '@/lib/types'

interface TimelineItem {
  id: string
  type: 'finding' | 'report'
  title: string
  content: string
  observation_type: string | null
  entity_name: string | null
  entity_type: string | null
  source_url: string | null
  timestamp: string
}

const OBS_TYPE_META: Record<string, { icon: typeof Newspaper; color: string; label: string }> = {
  news: { icon: Newspaper, color: 'text-blue-400', label: 'News' },
  feature_change: { icon: Wrench, color: 'text-emerald-400', label: 'Feature' },
  pricing_update: { icon: CurrencyDollar, color: 'text-amber-400', label: 'Pricing' },
  metric: { icon: TrendUp, color: 'text-cyan-400', label: 'Metric' },
  regulatory: { icon: Scales, color: 'text-red-400', label: 'Regulatory' },
  general: { icon: Lightbulb, color: 'text-zinc-400', label: 'Finding' },
}

export default function OverviewPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [project, setProject] = useState<ProjectDetail | null>(null)
  const [knowledge, setKnowledge] = useState<KnowledgeSummary | null>(null)
  const [timeline, setTimeline] = useState<TimelineItem[]>([])
  const [activeItems, setActiveItems] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(() => {
    return Promise.all([
      api.getProject(projectId),
      api.knowledgeSummary(projectId).catch(() => null),
      api.timeline(projectId, 15).catch(() => []),
      api.listWorkItems(projectId).catch(() => []),
    ]).then(([p, k, t, items]) => {
      setProject(p)
      setKnowledge(k)
      setTimeline(t)
      setActiveItems((items as any[]).filter((i: any) => i.status === 'in_progress'))
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [projectId])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  // Auto-refresh every 10s when agents are active
  useEffect(() => {
    if (activeItems.length === 0) return
    const interval = setInterval(fetchData, 10000)
    return () => clearInterval(interval)
  }, [activeItems.length, fetchData])

  const timeAgo = (ts: string) => {
    const diff = Date.now() - new Date(ts).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    const days = Math.floor(hrs / 24)
    if (days === 1) return 'yesterday'
    return `${days}d ago`
  }

  const formatDate = (ts: string) => {
    return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
              <div className="skeleton h-8 w-12 mb-2" />
              <div className="skeleton h-3 w-16" />
            </div>
          ))}
        </div>
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
          <div className="skeleton h-5 w-48 mb-4" />
          <div className="space-y-3">
            {[0,1,2].map(i => <div key={i} className="skeleton h-16 w-full rounded-lg" />)}
          </div>
        </div>
      </div>
    )
  }

  const stats = project?.stats
  const competitorCount = stats?.competitor_count ?? knowledge?.entity_count_by_type?.company ?? 0
  const findingCount = stats?.observation_count ?? knowledge?.total_observations ?? 0
  const reportCount = knowledge?.total_artifacts ?? 0

  return (
    <div className="space-y-6">
      {/* Clickable stats — each links to its detail page */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Link href={`/projects/${projectId}/competitors`} className="bg-zinc-900 border border-zinc-800 hover:border-emerald-500/30 rounded-xl p-4 transition-colors group">
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-1.5">
              <Binoculars size={14} className="text-zinc-500 group-hover:text-emerald-400 transition-colors" />
              <span className="text-xs text-zinc-500 uppercase tracking-wider">Competitors</span>
            </div>
            <ArrowRight size={12} className="text-zinc-700 group-hover:text-emerald-400 transition-colors" />
          </div>
          <div className="text-2xl font-semibold">{competitorCount}</div>
          <div className="text-xs text-zinc-600 mt-0.5">discovered</div>
        </Link>

        <Link href={`/projects/${projectId}/backlog`} className="bg-zinc-900 border border-zinc-800 hover:border-emerald-500/30 rounded-xl p-4 transition-colors group">
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-1.5">
              <Lightbulb size={14} className="text-zinc-500 group-hover:text-emerald-400 transition-colors" />
              <span className="text-xs text-zinc-500 uppercase tracking-wider">Findings</span>
            </div>
            <ArrowRight size={12} className="text-zinc-700 group-hover:text-emerald-400 transition-colors" />
          </div>
          <div className="text-2xl font-semibold">{findingCount}</div>
          <div className="text-xs text-zinc-600 mt-0.5">evidence-backed facts</div>
        </Link>

        <Link href={`/projects/${projectId}/intelligence`} className="bg-zinc-900 border border-zinc-800 hover:border-emerald-500/30 rounded-xl p-4 transition-colors group">
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-1.5">
              <FileText size={14} className="text-zinc-500 group-hover:text-emerald-400 transition-colors" />
              <span className="text-xs text-zinc-500 uppercase tracking-wider">Reports</span>
            </div>
            <ArrowRight size={12} className="text-zinc-700 group-hover:text-emerald-400 transition-colors" />
          </div>
          <div className="text-2xl font-semibold">{reportCount}</div>
          <div className="text-xs text-zinc-600 mt-0.5">competitor profiles & analyses</div>
        </Link>

        <Link href={`/projects/${projectId}/uat`} className="bg-zinc-900 border border-zinc-800 hover:border-emerald-500/30 rounded-xl p-4 transition-colors group">
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-1.5">
              <DeviceMobile size={14} className="text-zinc-500 group-hover:text-emerald-400 transition-colors" />
              <span className="text-xs text-zinc-500 uppercase tracking-wider">UAT</span>
            </div>
            <ArrowRight size={12} className="text-zinc-700 group-hover:text-emerald-400 transition-colors" />
          </div>
          <div className="text-2xl font-semibold">{stats?.screen_count ?? 0}</div>
          <div className="text-xs text-zinc-600 mt-0.5">screens mapped, {stats?.plan_count ?? 0} plans</div>
        </Link>
      </div>

      {/* Live agent activity */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        {activeItems.length > 0 ? (
          /* Show what's actively being researched */
          <div className="divide-y divide-zinc-800/60">
            {activeItems.map((item) => {
              const elapsed = item.started_at
                ? Math.floor((Date.now() - new Date(item.started_at).getTime()) / 1000)
                : 0
              const elapsedStr = elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`
              return (
                <div key={item.id} className="px-4 py-3">
                  <div className="flex items-center gap-2 mb-1.5">
                    <CircleNotch size={14} className="text-emerald-400 animate-spin shrink-0" />
                    <span className="text-xs font-medium text-emerald-400">
                      {item.agent_type.replace(/_/g, ' ')}
                    </span>
                    <span className="text-xs bg-zinc-800 text-zinc-500 px-1.5 py-0.5 rounded">
                      {item.category.replace(/_/g, ' ')}
                    </span>
                    <span className="text-xs text-zinc-600 tabular-nums ml-auto">{elapsedStr}</span>
                  </div>
                  <p className="text-sm text-zinc-300 pl-5">
                    {item.description.length > 150 ? item.description.slice(0, 150) + '...' : item.description}
                  </p>
                </div>
              )
            })}
          </div>
        ) : (
          /* Nothing running — show a prompt to go to Intelligence tab */
          <div className="px-4 py-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="h-2.5 w-2.5 rounded-full bg-zinc-600" />
              <span className="text-sm text-zinc-500">No research running right now</span>
            </div>
            <Link
              href={`/projects/${projectId}/intelligence`}
              className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
            >
              <Brain size={12} />
              Run agents
            </Link>
          </div>
        )}
      </div>

      {/* Product Timeline — the main content */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
            <Clock size={14} />
            Product Timeline
          </h2>
          {timeline.length > 0 && (
            <span className="text-xs text-zinc-600">Last updated {timeAgo(timeline[0].timestamp)}</span>
          )}
        </div>

        {timeline.length === 0 ? (
          <div className="bg-zinc-900 border border-dashed border-zinc-800 rounded-xl p-8 text-center">
            <Brain size={28} className="text-zinc-700 mx-auto mb-3" />
            <p className="text-sm text-zinc-500 mb-1">No findings yet</p>
            <p className="text-xs text-zinc-600">Start the agents above to begin building intelligence about your product and competitors.</p>
          </div>
        ) : (
          <div className="relative">
            {/* Vertical timeline line */}
            <div className="absolute left-[17px] top-2 bottom-2 w-px bg-zinc-800" />

            <div className="space-y-1">
              {timeline.map((item, idx) => {
                const meta = item.observation_type ? OBS_TYPE_META[item.observation_type] || OBS_TYPE_META.general : null
                const Icon = item.type === 'report' ? FileText : (meta?.icon ?? Lightbulb)
                const iconColor = item.type === 'report' ? 'text-violet-400' : (meta?.color ?? 'text-zinc-400')

                return (
                  <div key={item.id} className="relative pl-10 group">
                    {/* Timeline dot */}
                    <div className={`absolute left-2.5 top-4 w-2 h-2 rounded-full ${item.type === 'report' ? 'bg-violet-400' : (meta?.color?.replace('text-', 'bg-') ?? 'bg-zinc-400')}`} />

                    <div className="bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-xl p-4 transition-colors">
                      {/* Header row */}
                      <div className="flex items-start justify-between gap-3 mb-1.5">
                        <div className="flex items-center gap-2 min-w-0">
                          <Icon size={14} className={iconColor} weight="duotone" />
                          {item.type === 'report' ? (
                            <span className="text-xs bg-violet-500/10 text-violet-400 border border-violet-500/20 px-1.5 py-0.5 rounded">Report</span>
                          ) : meta ? (
                            <span className={`text-xs ${meta.color.replace('text-', 'bg-').replace('400', '500/10')} ${meta.color} border ${meta.color.replace('text-', 'border-').replace('400', '500/20')} px-1.5 py-0.5 rounded`}>
                              {meta.label}
                            </span>
                          ) : null}
                          {item.entity_name && (
                            <span className="text-sm font-medium text-zinc-200 truncate">{item.entity_name}</span>
                          )}
                        </div>
                        <span className="text-xs text-zinc-600 shrink-0 whitespace-nowrap">{formatDate(item.timestamp)}</span>
                      </div>

                      {/* Content */}
                      <p className="text-sm text-zinc-400 leading-relaxed">
                        {item.content.length > 250 ? item.content.slice(0, 250) + '...' : item.content}
                      </p>

                      {/* Source link */}
                      {item.source_url && (
                        <a
                          href={item.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-zinc-600 hover:text-emerald-400 mt-2 transition-colors"
                        >
                          <ArrowSquareOut size={10} />
                          {(() => {
                            try { return new URL(item.source_url).hostname } catch { return 'source' }
                          })()}
                        </a>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>

            {timeline.length >= 15 && (
              <Link
                href={`/projects/${projectId}/backlog`}
                className="block text-center text-xs text-zinc-500 hover:text-emerald-400 mt-3 transition-colors"
              >
                View full activity log
              </Link>
            )}
          </div>
        )}
      </div>

      {/* Quick actions */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {[
          { label: 'Ask a question', desc: '"How does our booking compare to Booking.com?"', href: `/projects/${projectId}/ask`, icon: ChatCircle },
          { label: 'View competitors', desc: `${competitorCount} companies tracked`, href: `/projects/${projectId}/competitors`, icon: Binoculars },
          { label: 'Manage agents', desc: 'Run agents, view backlog, track progress', href: `/projects/${projectId}/intelligence`, icon: Brain },
        ].map((action) => {
          const Icon = action.icon
          return (
            <Link
              key={action.label}
              href={action.href}
              className="bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-xl p-4 flex items-start gap-3 transition-colors group"
            >
              <Icon size={18} className="text-emerald-400 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="font-medium text-sm flex items-center gap-1.5">
                  {action.label}
                  <ArrowRight size={12} className="text-zinc-600 group-hover:text-emerald-400 transition-colors" />
                </div>
                <p className="text-xs text-zinc-500 mt-0.5">{action.desc}</p>
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
