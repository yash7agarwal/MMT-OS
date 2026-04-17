'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, CheckCircle, Warning, XCircle, Clock, Info } from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { UatRun } from '@/lib/types'
import { FrameComparisonCard } from '@/components/FrameComparisonCard'

export default function UatRunDetailPage({
  params,
}: {
  params: { id: string; runId: string }
}) {
  const projectId = parseInt(params.id, 10)
  const runId = parseInt(params.runId, 10)
  const [run, setRun] = useState<UatRun | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .getUatRun(runId)
      .then(setRun)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))

    // Poll while running
    const interval = setInterval(async () => {
      try {
        const r = await api.getUatRun(runId)
        setRun(r)
        if (r.status === 'completed' || r.status === 'failed') {
          clearInterval(interval)
        }
      } catch {
        /* swallow -- next tick */
      }
    }, 3000)
    return () => clearInterval(interval)
  }, [runId])

  if (loading) return (
    <div className="space-y-4">
      <div className="skeleton h-4 w-32" />
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <div className="skeleton h-6 w-40 mb-3" />
        <div className="skeleton h-4 w-64 mb-2" />
        <div className="skeleton h-3 w-48" />
      </div>
    </div>
  )
  if (error) return <p className="text-red-400 text-sm">Error: {error}</p>
  if (!run) return <p className="text-zinc-500 text-sm">Run not found</p>

  const scorePct =
    run.overall_match_score !== null ? `${(run.overall_match_score * 100).toFixed(0)}%` : '--'

  return (
    <div>
      <Link
        href={`/projects/${projectId}/runs`}
        className="inline-flex items-center gap-1.5 text-zinc-500 hover:text-zinc-300 text-sm mb-4 transition-colors duration-150"
      >
        <ArrowLeft size={14} />
        All runs
      </Link>

      {/* Header */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 mb-6">
        <div className="flex items-start justify-between gap-6">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-2 flex-wrap">
              <h1 className="text-2xl font-semibold tracking-tight">Run #{run.id}</h1>
              <StatusBadge status={run.status} />
              {run.apk_version && (
                <span className="text-xs text-zinc-500 font-mono bg-zinc-800 px-2 py-0.5 rounded">
                  v{run.apk_version}
                </span>
              )}
            </div>
            {run.feature_description && (
              <p className="text-zinc-400 italic text-sm mb-1">&quot;{run.feature_description}&quot;</p>
            )}
            {run.figma_file_id && (
              <p className="text-xs text-zinc-600 font-mono">Figma: {run.figma_file_id}</p>
            )}
            <p className="text-xs text-zinc-600 mt-1">
              Started {new Date(run.started_at).toLocaleString()}
              {run.completed_at && ` \u00b7 Completed ${new Date(run.completed_at).toLocaleString()}`}
            </p>
          </div>
          <div className="text-center shrink-0">
            <div className="text-5xl font-bold text-zinc-200">{scorePct}</div>
            <div className="text-xs text-zinc-500 uppercase tracking-wider mt-1">match</div>
          </div>
        </div>

        {/* Counts */}
        <div className="flex gap-6 mt-5 pt-5 border-t border-zinc-800">
          <Stat label="Total" value={run.total_frames} />
          <Stat label="Matched" value={run.matched} icon={<CheckCircle size={14} className="text-green-400" />} />
          <Stat label="Differs" value={run.mismatched} icon={<Warning size={14} className="text-amber-400" />} />
          <Stat label="Unreachable" value={run.unreachable} icon={<XCircle size={14} className="text-red-400" />} />
          {run.report_md_path && (
            <a
              href={api.uatReportMdUrl(run.id)}
              target="_blank"
              rel="noreferrer"
              className="ml-auto text-sm text-emerald-400 hover:text-emerald-300 self-end transition-colors duration-150"
            >
              Download report.md
            </a>
          )}
        </div>
      </div>

      {/* Error state */}
      {run.status === 'failed' && run.error && (
        <div className="border border-red-500/20 bg-red-500/10 text-red-200 p-4 rounded-xl mb-6">
          <p className="font-semibold mb-2 text-sm">Run failed</p>
          <pre className="text-xs whitespace-pre-wrap font-mono">{run.error.slice(0, 800)}</pre>
        </div>
      )}

      {run.status === 'running' && (
        <div className="border border-cyan-500/20 bg-cyan-500/10 text-cyan-200 p-4 rounded-xl mb-6 flex items-center gap-2">
          <Clock size={16} className="text-cyan-400" />
          <span className="text-sm">Run in progress. Page auto-refreshes every 3 seconds.</span>
        </div>
      )}

      {/* Per-frame cards */}
      {run.frame_results && run.frame_results.length > 0 && (
        <div className="space-y-4">
          <h2 className="text-lg font-medium">Per-frame comparison</h2>
          {run.frame_results.map((fr) => (
            <FrameComparisonCard key={fr.id} runId={run.id} frame={fr} />
          ))}
        </div>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: UatRun['status'] }) {
  const styles: Record<string, string> = {
    pending: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20',
    running: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
    completed: 'bg-green-500/10 text-green-400 border-green-500/20',
    failed: 'bg-red-500/10 text-red-400 border-red-500/20',
  }
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${styles[status] || styles.pending}`}>
      {status}
    </span>
  )
}

function Stat({ label, value, icon }: { label: string; value: number; icon?: React.ReactNode }) {
  return (
    <div className="text-center">
      <div className="text-2xl font-semibold flex items-center justify-center gap-1">
        {icon}
        {value}
      </div>
      <div className="text-xs text-zinc-500 uppercase tracking-wide">{label}</div>
    </div>
  )
}
