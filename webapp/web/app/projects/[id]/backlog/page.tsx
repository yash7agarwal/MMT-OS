'use client'

import { useEffect, useState } from 'react'
import {
  Queue,
  Lightning,
  CheckCircle,
  XCircle,
  Clock,
  Funnel,
  Brain,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { WorkItem, AgentSession } from '@/lib/types'

type Tab = 'items' | 'sessions'
type StatusFilter = 'all' | 'pending' | 'in_progress' | 'completed' | 'failed'
type AgentFilter = 'all' | 'competitive_intel' | 'industry_research' | 'ux_intel'

const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-zinc-500/10 text-zinc-400 border border-zinc-500/20',
  in_progress: 'bg-amber-500/10 text-amber-400 border border-amber-500/20',
  completed: 'bg-green-500/10 text-green-400 border border-green-500/20',
  failed: 'bg-red-500/10 text-red-400 border border-red-500/20',
}

const AGENT_COLORS: Record<string, string> = {
  competitive_intel: 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20',
  industry_research: 'bg-violet-500/10 text-violet-400 border border-violet-500/20',
  ux_intel: 'bg-rose-500/10 text-rose-400 border border-rose-500/20',
}

function formatDuration(start: string, end: string | null): string {
  if (!end) return 'running...'
  const ms = new Date(end).getTime() - new Date(start).getTime()
  const secs = Math.floor(ms / 1000)
  if (secs < 60) return `${secs}s`
  const mins = Math.floor(secs / 60)
  const remSecs = secs % 60
  if (mins < 60) return `${mins}m ${remSecs}s`
  const hrs = Math.floor(mins / 60)
  return `${hrs}h ${mins % 60}m`
}

function formatTimestamp(ts: string): string {
  return new Date(ts).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function BacklogPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)

  const [tab, setTab] = useState<Tab>('items')
  const [items, setItems] = useState<WorkItem[]>([])
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [agentFilter, setAgentFilter] = useState<AgentFilter>('all')

  const refresh = async () => {
    setLoading(true)
    try {
      const [w, s] = await Promise.all([
        api.listWorkItems(projectId),
        api.listSessions(projectId),
      ])
      setItems(w)
      setSessions(s)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [projectId])

  const counts = {
    pending: items.filter((i) => i.status === 'pending').length,
    completed: items.filter((i) => i.status === 'completed').length,
    failed: items.filter((i) => i.status === 'failed').length,
  }

  const filteredItems = items.filter((i) => {
    if (statusFilter !== 'all' && i.status !== statusFilter) return false
    if (agentFilter !== 'all' && i.agent_type !== agentFilter) return false
    return true
  })

  const filteredSessions = sessions.filter((s) => {
    if (agentFilter !== 'all' && s.agent_type !== agentFilter) return false
    return true
  })

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-5xl mx-auto px-4 py-12">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center gap-2 mb-1">
            <Queue size={24} className="text-emerald-400" />
            <h1 className="text-2xl font-semibold tracking-tight">Agent Backlog</h1>
          </div>
          <div className="flex items-center gap-3 text-sm text-zinc-500">
            <span className="inline-flex items-center gap-1">
              <Clock size={14} /> {counts.pending} pending
            </span>
            <span className="inline-flex items-center gap-1">
              <CheckCircle size={14} className="text-green-400" /> {counts.completed} completed
            </span>
            <span className="inline-flex items-center gap-1">
              <XCircle size={14} className="text-red-400" /> {counts.failed} failed
            </span>
          </div>
        </div>

        {/* Tab bar */}
        <div className="flex gap-1 mb-6 bg-zinc-900 border border-zinc-800 rounded-lg p-1 w-fit">
          {(['items', 'sessions'] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 rounded-md text-sm font-medium transition-colors duration-150 ${
                tab === t
                  ? 'bg-zinc-800 text-zinc-100'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {t === 'items' ? 'Work Items' : 'Sessions'}
            </button>
          ))}
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <Funnel size={14} className="text-zinc-600" />

          {tab === 'items' && (
            <div className="flex gap-1.5 flex-wrap">
              {(['all', 'pending', 'in_progress', 'completed', 'failed'] as StatusFilter[]).map(
                (s) => (
                  <button
                    key={s}
                    onClick={() => setStatusFilter(s)}
                    className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors duration-150 ${
                      statusFilter === s
                        ? 'bg-emerald-600 text-white'
                        : 'bg-zinc-900 border border-zinc-800 text-zinc-500 hover:text-zinc-300'
                    }`}
                  >
                    {s === 'all' ? 'All' : s.replace('_', ' ')}
                  </button>
                )
              )}
            </div>
          )}

          <div className="h-4 w-px bg-zinc-800" />

          <div className="flex gap-1.5 flex-wrap">
            {(['all', 'competitive_intel', 'industry_research', 'ux_intel'] as AgentFilter[]).map(
              (a) => (
                <button
                  key={a}
                  onClick={() => setAgentFilter(a)}
                  className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors duration-150 ${
                    agentFilter === a
                      ? 'bg-emerald-600 text-white'
                      : 'bg-zinc-900 border border-zinc-800 text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  {a === 'all' ? 'All agents' : a.replace(/_/g, ' ')}
                </button>
              )
            )}
          </div>
        </div>

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-16">
            <div className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-sm text-zinc-500 ml-3">Loading...</span>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4">
            <p className="text-sm text-red-400">{error}</p>
          </div>
        )}

        {/* Work Items view */}
        {!loading && !error && tab === 'items' && (
          <div className="space-y-2">
            {filteredItems.length === 0 ? (
              <p className="text-sm text-zinc-500 text-center py-12">
                No work items match the current filters.
              </p>
            ) : (
              filteredItems.map((item) => (
                <div
                  key={item.id}
                  className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 flex items-start gap-3"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                          STATUS_COLORS[item.status] || STATUS_COLORS.pending
                        }`}
                      >
                        {item.status.replace('_', ' ')}
                      </span>
                      <span className="text-xs font-mono font-semibold text-zinc-400">
                        P{item.priority}
                      </span>
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                          AGENT_COLORS[item.agent_type] || 'bg-zinc-800 text-zinc-400 border border-zinc-700'
                        }`}
                      >
                        {item.agent_type.replace(/_/g, ' ')}
                      </span>
                      <span className="text-xs text-zinc-600">{item.category}</span>
                    </div>
                    <p className="text-sm text-zinc-300 truncate">{item.description}</p>
                    {item.result_summary && (
                      <p className="text-xs text-zinc-500 mt-1 truncate">{item.result_summary}</p>
                    )}
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-xs text-zinc-600">{formatTimestamp(item.created_at)}</p>
                    {item.completed_at && (
                      <p className="text-xs text-zinc-700 mt-0.5">
                        Done {formatTimestamp(item.completed_at)}
                      </p>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {/* Sessions view */}
        {!loading && !error && tab === 'sessions' && (
          <div className="space-y-2">
            {filteredSessions.length === 0 ? (
              <p className="text-sm text-zinc-500 text-center py-12">
                No sessions match the current filters.
              </p>
            ) : (
              filteredSessions.map((s) => (
                <div
                  key={s.id}
                  className="bg-zinc-900 border border-zinc-800 rounded-xl p-4"
                >
                  <div className="flex items-center justify-between gap-3 mb-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                          AGENT_COLORS[s.agent_type] || 'bg-zinc-800 text-zinc-400 border border-zinc-700'
                        }`}
                      >
                        {s.agent_type.replace(/_/g, ' ')}
                      </span>
                      <span className="text-xs text-zinc-500">
                        Session #{s.id}
                      </span>
                    </div>
                    <span className="text-xs text-zinc-600">
                      {formatDuration(s.started_at, s.completed_at)}
                    </span>
                  </div>

                  <div className="flex items-center gap-4 text-xs text-zinc-500 mb-2">
                    <span className="inline-flex items-center gap-1">
                      <CheckCircle size={12} className="text-green-400" />
                      {s.items_completed} completed
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <XCircle size={12} className="text-red-400" />
                      {s.items_failed} failed
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <Brain size={12} className="text-emerald-400" />
                      {s.knowledge_added} knowledge added
                    </span>
                  </div>

                  {s.session_summary && (
                    <p className="text-sm text-zinc-400">{s.session_summary}</p>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  )
}
