'use client'

import { useEffect, useState } from 'react'
import { FilePdf, FileXls, FileText, Sparkle } from '@phosphor-icons/react'
import { api } from '@/lib/api'
import { ErrorBanner } from '@/components/ErrorBanner'
import { GenerateReportModal } from '@/components/GenerateReportModal'

type RecentReport = {
  artifact_id: number
  title: string
  generated_at: string
  content_hash: string | null
  stats: Record<string, number>
  rec_count: number
  loupe_runs_included: number
}

export default function ReportsPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [reports, setReports] = useState<RecentReport[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [modalOpen, setModalOpen] = useState(false)

  const refresh = () => {
    setError(null)
    api
      .recentReports(projectId)
      .then(setReports)
      .catch((e: Error) => setError(e.message || String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(refresh, [projectId])

  const fmt = (iso: string | null) => {
    if (!iso) return '—'
    try {
      const d = new Date(iso)
      return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      })
    } catch {
      return iso
    }
  }

  return (
    <div>
      <div className="flex items-start justify-between mb-6">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Executive reports</h2>
          <p className="text-zinc-500 text-sm mt-1">
            Management-grade summaries of everything Prism has gathered. PDF + Excel,
            with clickable sources and an evidence-anchored narrative.
          </p>
        </div>
        <button
          onClick={() => setModalOpen(true)}
          className="shrink-0 inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        >
          <Sparkle size={14} weight="fill" />
          Generate report
        </button>
      </div>

      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      {loading && (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-20 rounded-xl" />
          ))}
        </div>
      )}

      {!loading && reports.length === 0 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-10 text-center">
          <FileText size={36} className="mx-auto text-zinc-700 mb-3" />
          <h3 className="text-zinc-300 font-medium mb-1">No reports yet</h3>
          <p className="text-sm text-zinc-500 max-w-sm mx-auto">
            Click <span className="text-emerald-400 font-medium">Generate report</span> to
            create your first management-grade brief from this project's knowledge graph.
            Generation takes ~60–90s for a fresh report.
          </p>
        </div>
      )}

      {!loading && reports.length > 0 && (
        <div className="space-y-3">
          {reports.map((r) => (
            <div
              key={r.artifact_id}
              className="rounded-xl border border-zinc-800 bg-zinc-900 px-5 py-4 flex items-center justify-between gap-4 hover:border-zinc-700 transition-colors"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <FileText size={14} className="text-emerald-400 shrink-0" />
                  <h3 className="text-zinc-200 text-sm font-medium truncate">{r.title}</h3>
                </div>
                <div className="flex items-center gap-3 text-xs text-zinc-500">
                  <span>{fmt(r.generated_at)}</span>
                  <span>·</span>
                  <span>{r.stats?.competitor_count ?? 0} competitors</span>
                  <span>·</span>
                  <span>{r.stats?.observation_count ?? 0} observations</span>
                  <span>·</span>
                  <span>{r.rec_count} recommendations</span>
                  {r.loupe_runs_included > 0 && (
                    <>
                      <span>·</span>
                      <span className="text-emerald-400">UAT included</span>
                    </>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <a
                  href={api.reportDownloadUrl(r.artifact_id, 'pdf')}
                  className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
                >
                  <FilePdf size={13} /> PDF
                </a>
                <a
                  href={api.reportDownloadUrl(r.artifact_id, 'xlsx')}
                  className="inline-flex items-center gap-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
                >
                  <FileXls size={13} /> Excel
                </a>
              </div>
            </div>
          ))}
        </div>
      )}

      <GenerateReportModal
        projectId={projectId}
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onComplete={refresh}
      />
    </div>
  )
}
