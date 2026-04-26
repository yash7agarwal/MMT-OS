'use client'

import { useEffect, useState } from 'react'
import { X, FilePdf, FileXls, CircleNotch, CheckCircle, Warning } from '@phosphor-icons/react'
import { api } from '@/lib/api'

type Status = 'idle' | 'generating' | 'done' | 'failed'

export function GenerateReportModal({
  projectId,
  open,
  onClose,
  onComplete,
}: {
  projectId: number
  open: boolean
  onClose: () => void
  onComplete: () => void
}) {
  const [pdf, setPdf] = useState(true)
  const [xlsx, setXlsx] = useState(true)
  const [includeLoupe, setIncludeLoupe] = useState(true)
  const [status, setStatus] = useState<Status>('idle')
  const [progress, setProgress] = useState<string>('')
  const [artifactId, setArtifactId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Reset on close
  useEffect(() => {
    if (!open) {
      setStatus('idle')
      setProgress('')
      setArtifactId(null)
      setError(null)
    }
  }, [open])

  if (!open) return null

  async function start() {
    setError(null)
    const formats: string[] = []
    if (pdf) formats.push('pdf')
    if (xlsx) formats.push('xlsx')
    if (formats.length === 0) {
      setError('Choose at least one format.')
      return
    }
    setStatus('generating')
    setProgress('Queuing…')
    try {
      const { job_id } = await api.generateReport(projectId, formats, includeLoupe)
      // Poll every 2s until terminal
      const poll = async () => {
        const job = await api.reportJobStatus(job_id)
        setProgress(job.progress || job.status)
        if (job.status === 'done' && job.artifact_id) {
          setStatus('done')
          setArtifactId(job.artifact_id)
          onComplete()
        } else if (job.status === 'failed') {
          setStatus('failed')
          setError(job.error || 'Generation failed')
        } else {
          setTimeout(poll, 2000)
        }
      }
      setTimeout(poll, 1500)
    } catch (e) {
      setStatus('failed')
      setError((e as Error).message)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-md shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 border-b border-zinc-800 flex items-center justify-between">
          <h2 className="text-zinc-100 font-semibold tracking-tight">Generate executive report</h2>
          <button
            onClick={onClose}
            className="text-zinc-500 hover:text-zinc-300"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5">
          {status === 'idle' && (
            <div className="space-y-4">
              <p className="text-sm text-zinc-400">
                Generates a McKinsey-grade summary of everything Prism knows about
                this company. Uses Claude to synthesize narrative; observations
                appear with clickable sources.
              </p>

              <div className="space-y-3">
                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={pdf}
                    onChange={(e) => setPdf(e.target.checked)}
                    className="w-4 h-4 accent-emerald-500"
                  />
                  <FilePdf size={18} className="text-emerald-400" weight="duotone" />
                  <span className="text-sm text-zinc-200">PDF report (cover, narrative, charts)</span>
                </label>

                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={xlsx}
                    onChange={(e) => setXlsx(e.target.checked)}
                    className="w-4 h-4 accent-emerald-500"
                  />
                  <FileXls size={18} className="text-emerald-400" weight="duotone" />
                  <span className="text-sm text-zinc-200">Excel workbook (9 tabs, hyperlinked sources)</span>
                </label>

                <label className="flex items-center gap-3 cursor-pointer pt-2 border-t border-zinc-800">
                  <input
                    type="checkbox"
                    checked={includeLoupe}
                    onChange={(e) => setIncludeLoupe(e.target.checked)}
                    className="w-4 h-4 accent-emerald-500"
                  />
                  <span className="text-xs text-zinc-400">Include Loupe UAT data (when reachable)</span>
                </label>
              </div>

              {error && (
                <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-xs rounded-lg p-3">
                  {error}
                </div>
              )}
            </div>
          )}

          {status === 'generating' && (
            <div className="py-8 flex flex-col items-center text-center gap-3">
              <CircleNotch size={28} className="animate-spin text-emerald-400" />
              <div className="text-sm text-zinc-200 font-medium">Generating report</div>
              <div className="text-xs text-zinc-500 max-w-xs">{progress || 'Working…'}</div>
              <div className="text-xs text-zinc-600 mt-2">~60–90s for a fresh report</div>
            </div>
          )}

          {status === 'done' && artifactId && (
            <div className="py-2 flex flex-col items-center text-center gap-3">
              <CheckCircle size={32} className="text-emerald-400" weight="fill" />
              <div className="text-sm text-zinc-200 font-medium">Report ready</div>
              <div className="flex gap-2 mt-2">
                {pdf && (
                  <a
                    href={api.reportDownloadUrl(artifactId, 'pdf')}
                    className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium px-3 py-1.5 rounded-lg transition-colors"
                  >
                    <FilePdf size={14} /> PDF
                  </a>
                )}
                {xlsx && (
                  <a
                    href={api.reportDownloadUrl(artifactId, 'xlsx')}
                    className="inline-flex items-center gap-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-sm font-medium px-3 py-1.5 rounded-lg transition-colors"
                  >
                    <FileXls size={14} /> Excel
                  </a>
                )}
              </div>
            </div>
          )}

          {status === 'failed' && (
            <div className="py-6 flex flex-col items-center text-center gap-3">
              <Warning size={28} className="text-red-400" weight="fill" />
              <div className="text-sm text-zinc-200 font-medium">Generation failed</div>
              <div className="text-xs text-red-300/80 max-w-xs font-mono break-all">
                {error}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        {status === 'idle' && (
          <div className="px-6 py-4 border-t border-zinc-800 flex justify-end gap-2">
            <button
              onClick={onClose}
              className="text-sm text-zinc-400 hover:text-zinc-200 px-4 py-2 rounded-lg"
            >
              Cancel
            </button>
            <button
              onClick={start}
              className="bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              Generate
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
