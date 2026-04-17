'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Plus, CheckCircle, Warning, XCircle } from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { UatRunSummary, UatRunStatus } from '@/lib/types'

const STATUS_STYLES: Record<UatRunStatus, string> = {
  pending:   'bg-zinc-500/10 text-zinc-400 border border-zinc-500/20',
  running:   'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20',
  completed: 'bg-green-500/10 text-green-400 border border-green-500/20',
  failed:    'bg-red-500/10 text-red-400 border border-red-500/20',
}

export default function RunsListPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [runs, setRuns] = useState<UatRunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .listUatRuns(projectId)
      .then(setRuns)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [projectId])

  return (
    <div>
      <Link
        href={`/projects/${projectId}`}
        className="inline-flex items-center gap-1.5 text-zinc-500 hover:text-zinc-300 text-sm mb-4 transition-colors duration-150"
      >
        <ArrowLeft size={14} />
        Back to project
      </Link>
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">UAT Runs</h1>
          <p className="text-zinc-400 mt-1 text-sm">
            Each run installs the APK, navigates autonomously through every Figma frame, and produces a comparison report.
          </p>
        </div>
        <Link
          href={`/projects/${projectId}/runs/new`}
          className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg font-medium text-sm transition-colors duration-150 active:scale-[0.98] active:translate-y-[1px]"
        >
          <Plus size={16} weight="bold" />
          Start UAT run
        </Link>
      </div>

      {loading && (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
              <div className="flex items-center gap-3">
                <div className="skeleton h-4 w-20" />
                <div className="skeleton h-4 w-16" />
              </div>
              <div className="skeleton h-3 w-48 mt-2" />
              <div className="flex gap-4 mt-2">
                <div className="skeleton h-3 w-12" />
                <div className="skeleton h-3 w-12" />
                <div className="skeleton h-3 w-12" />
              </div>
            </div>
          ))}
        </div>
      )}
      {error && <p className="text-red-400 text-sm">Error: {error}</p>}

      {!loading && !error && runs.length === 0 && (
        <div className="border border-dashed border-zinc-800 rounded-xl p-12 text-center">
          <XCircle size={32} className="text-zinc-600 mx-auto mb-3" />
          <p className="text-zinc-400 text-sm mb-4">No UAT runs yet.</p>
          <Link
            href={`/projects/${projectId}/runs/new`}
            className="text-emerald-400 hover:text-emerald-300 font-medium text-sm"
          >
            Start your first run
          </Link>
        </div>
      )}

      {runs.length > 0 && (
        <div className="space-y-2">
          {runs.map((r, index) => {
            const score = r.overall_match_score !== null ? `${(r.overall_match_score * 100).toFixed(0)}%` : '--'
            return (
              <Link
                key={r.id}
                href={`/projects/${projectId}/runs/${r.id}`}
                className="bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-xl p-4 flex items-center justify-between transition-colors duration-200 gap-3 animate-fade-in-up"
                style={{ animationDelay: `${index * 80}ms` } as React.CSSProperties}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3">
                    <span className="font-medium text-sm">Run #{r.id}</span>
                    {r.apk_version && <span className="text-xs text-zinc-500 font-mono">v{r.apk_version}</span>}
                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${STATUS_STYLES[r.status]}`}>
                      {r.status}
                    </span>
                  </div>
                  {r.feature_description && (
                    <p className="text-xs text-zinc-500 truncate mt-1 italic">&quot;{r.feature_description}&quot;</p>
                  )}
                  <div className="flex gap-4 mt-2 text-xs text-zinc-600">
                    <span className="inline-flex items-center gap-1"><CheckCircle size={12} className="text-green-400" /> {r.matched}</span>
                    <span className="inline-flex items-center gap-1"><Warning size={12} className="text-amber-400" /> {r.mismatched}</span>
                    <span className="inline-flex items-center gap-1"><XCircle size={12} className="text-red-400" /> {r.unreachable}</span>
                    <span className="text-zinc-500">of {r.total_frames}</span>
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className="text-2xl font-semibold text-zinc-300">{score}</div>
                  <div className="text-xs text-zinc-600">
                    {new Date(r.started_at).toLocaleDateString()}
                  </div>
                </div>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}
