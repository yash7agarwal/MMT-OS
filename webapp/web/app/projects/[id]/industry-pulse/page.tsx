'use client'

import { useEffect, useRef, useState } from 'react'
import { Pulse, ArrowsClockwise, FolderOpen, CheckCircle, Warning, XCircle, CaretDown, CaretUp } from '@phosphor-icons/react'
import { api } from '@/lib/api'
import { ErrorBanner } from '@/components/ErrorBanner'

// Minimal markdown renderer — sufficient for the synthesis output, which
// uses h1/h2/h3 + bullets + paragraphs. We deliberately avoid pulling in a
// full markdown lib to keep the bundle small.
function MdLite({ text }: { text: string }) {
  const lines = text.split('\n')
  const blocks: React.ReactNode[] = []
  let listBuffer: string[] = []

  const flushList = () => {
    if (listBuffer.length === 0) return
    blocks.push(
      <ul key={`l-${blocks.length}`} className="list-disc pl-5 space-y-1.5 text-sm text-zinc-300 my-2">
        {listBuffer.map((it, i) => <li key={i}>{renderInline(it)}</li>)}
      </ul>
    )
    listBuffer = []
  }

  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i]
    if (/^\s*[-*]\s+/.test(ln)) {
      listBuffer.push(ln.replace(/^\s*[-*]\s+/, ''))
      continue
    }
    flushList()
    if (/^#\s+/.test(ln)) {
      blocks.push(<h1 key={`h-${blocks.length}`} className="text-xl font-semibold text-zinc-100 mt-4 mb-2">{ln.replace(/^#\s+/, '')}</h1>)
    } else if (/^##\s+/.test(ln)) {
      blocks.push(<h2 key={`h-${blocks.length}`} className="text-base font-semibold text-emerald-400 mt-4 mb-2">{ln.replace(/^##\s+/, '')}</h2>)
    } else if (/^###\s+/.test(ln)) {
      blocks.push(<h3 key={`h-${blocks.length}`} className="text-sm font-semibold text-zinc-200 mt-3 mb-1">{ln.replace(/^###\s+/, '')}</h3>)
    } else if (ln.trim()) {
      blocks.push(<p key={`p-${blocks.length}`} className="text-sm text-zinc-300 leading-relaxed my-1">{renderInline(ln)}</p>)
    }
  }
  flushList()
  return <div>{blocks}</div>
}

function renderInline(text: string): React.ReactNode {
  // **bold** only — keep it minimal
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**')) {
      return <strong key={i} className="text-zinc-100">{p.slice(2, -2)}</strong>
    }
    return <span key={i}>{p}</span>
  })
}

export default function IndustryPulsePage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [data, setData] = useState<{
    competitor_count: number
    synthesis: string
    cached?: boolean
    generated_at?: string
    message?: string
  } | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // v0.21.1 — bulk upload state
  const [uploading, setUploading] = useState(false)
  const [manifest, setManifest] = useState<{
    matched_count: number
    unmatched_count: number
    failed_count: number
    synthesized_profiles: number
    synthesizing?: boolean
    synthesizing_count?: number
    matched: any[]
    unmatched: any[]
    failed: any[]
  } | null>(null)
  const [showManifest, setShowManifest] = useState(false)
  const [competitors, setCompetitors] = useState<{ id: number; name: string }[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleBulkUpload = async (filesIn: FileList | null) => {
    if (!filesIn || filesIn.length === 0) return
    const pdfs = Array.from(filesIn).filter(f => f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf'))
    if (pdfs.length === 0) {
      setError('No PDFs in selection. Drop a folder of annual reports or pick multiple PDF files.')
      return
    }
    setUploading(true)
    setError(null)
    setManifest(null)
    try {
      const r = await api.bulkUploadReports(projectId, pdfs, true)
      setManifest(r)
      setShowManifest(true)
      // Reload Industry Pulse since synthesis ran in-line.
      await load()
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleReassign = async (artifactId: number, entityId: number) => {
    if (!manifest) return
    try {
      await api.reassignArtifact(artifactId, entityId)
      // Move the unmatched row into matched in local state
      const row = manifest.unmatched.find(r => r.artifact_id === artifactId)
      if (row) {
        const ent = competitors.find(c => c.id === entityId)
        row.matched_entity_id = entityId
        row.matched_entity_name = ent?.name || `id=${entityId}`
        row.match_confidence = 'high'
        row.match_method = 'manual'
        setManifest({
          ...manifest,
          matched: [...manifest.matched, row],
          unmatched: manifest.unmatched.filter(r => r.artifact_id !== artifactId),
          matched_count: manifest.matched_count + 1,
          unmatched_count: manifest.unmatched_count - 1,
        })
      }
    } catch (e: any) {
      setError(`Reassign failed: ${e.message || e}`)
    }
  }

  const load = async () => {
    setError(null)
    try {
      const r = await api.industryPulse(projectId)
      setData(r)
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => { load() }, [projectId])

  // Load competitors for the reassign dropdown.
  useEffect(() => {
    api.listCompetitors(projectId)
      .then((cs: any) => setCompetitors(cs.map((c: any) => ({ id: c.id, name: c.name }))))
      .catch(() => { /* tolerate */ })
  }, [projectId])

  const handleRefresh = async () => {
    setRefreshing(true)
    await load()
  }

  if (loading) return <div className="skeleton h-64 w-full rounded-xl" />
  if (error) return <ErrorBanner message={error} />

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <Pulse size={20} className="text-emerald-400" />
          <h1 className="text-xl font-semibold tracking-tight text-zinc-100">Industry Pulse</h1>
          {data && (
            <span className="text-xs text-zinc-500">
              {data.competitor_count} profile{data.competitor_count === 1 ? '' : 's'} synthesized
              {data.cached && ' · cached'}
              {data.generated_at && ` · ${new Date(data.generated_at).toLocaleString()}`}
            </span>
          )}
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700 disabled:opacity-50 transition-colors"
          title="Re-run cross-cut synthesis. Hits the LLM — takes ~10-30s."
        >
          <ArrowsClockwise size={12} className={refreshing ? 'animate-spin' : ''} />
          {refreshing ? 'Synthesizing…' : 'Refresh'}
        </button>
      </div>

      <p className="text-sm text-zinc-500 mb-5 max-w-3xl">
        Cross-cut synthesis across all competitor business-history profiles in this project.
        Identifies dominant business models, margin patterns, contrarian themes, and risk
        concentrations spanning the competitive set. Upload more annual reports to deepen this view.
      </p>

      {/* v0.21.1: Bulk folder upload — drop in mixed annual / quarterly reports
           for many competitors, auto-classify each one, and synthesize. */}
      <div className="mb-5 bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <div className="flex items-center gap-3 mb-2">
          <FolderOpen size={18} className="text-emerald-400" />
          <h2 className="text-sm font-medium text-zinc-100">Bulk upload reports</h2>
        </div>
        <p className="text-xs text-zinc-500 mb-3 max-w-2xl leading-relaxed">
          Pick a folder (or multi-select PDFs) — annuals + quarterlies for any of your
          competitors. We extract text, match each PDF to the right competitor by filename
          first, then LLM-disambiguate when filenames are ambiguous. Period (FY / Qx) is
          parsed by regex — no LLM hallucination on dates. Unmatched files surface in the
          manifest below for manual reassign. Multi-period uploads automatically aggregate.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <label className={`inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium border cursor-pointer transition-colors ${uploading ? 'opacity-50 cursor-not-allowed bg-zinc-800 text-zinc-400 border-zinc-700' : 'bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border-emerald-500/20'}`}>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/pdf"
              multiple
              // @ts-expect-error — webkitdirectory is non-standard but enables folder picking on Chromium browsers.
              webkitdirectory=""
              directory=""
              disabled={uploading}
              onChange={(e) => handleBulkUpload(e.target.files)}
              className="hidden"
            />
            <FolderOpen size={14} />
            {uploading ? 'Processing…' : 'Pick a folder'}
          </label>
          <label className={`inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium border cursor-pointer transition-colors ${uploading ? 'opacity-50 cursor-not-allowed bg-zinc-800 text-zinc-400 border-zinc-700' : 'bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border-zinc-700'}`}>
            <input
              type="file"
              accept="application/pdf"
              multiple
              disabled={uploading}
              onChange={(e) => handleBulkUpload(e.target.files)}
              className="hidden"
            />
            … or pick multiple PDFs
          </label>
        </div>
        {uploading && (
          <p className="text-xs text-zinc-500 mt-3">
            Extracting + classifying + synthesizing. Each PDF takes a few seconds for
            extraction, plus ~10–30s synthesis per matched competitor (parallel).
          </p>
        )}
      </div>

      {manifest && (
        <div className="mb-5 bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
          <button
            onClick={() => setShowManifest(s => !s)}
            className="w-full px-5 py-4 flex items-center justify-between hover:bg-zinc-800/40 transition-colors"
          >
            <div className="flex items-center gap-4 text-sm">
              <span className="text-zinc-100 font-medium">Upload manifest</span>
              <span className="inline-flex items-center gap-1 text-emerald-400">
                <CheckCircle size={12} weight="fill" /> {manifest.matched_count} matched
              </span>
              {manifest.unmatched_count > 0 && (
                <span className="inline-flex items-center gap-1 text-amber-400">
                  <Warning size={12} weight="fill" /> {manifest.unmatched_count} unmatched
                </span>
              )}
              {manifest.failed_count > 0 && (
                <span className="inline-flex items-center gap-1 text-red-400">
                  <XCircle size={12} weight="fill" /> {manifest.failed_count} failed
                </span>
              )}
              {manifest.synthesizing ? (
                <span className="text-cyan-400">
                  · {manifest.synthesizing_count} profile{manifest.synthesizing_count === 1 ? '' : 's'} synthesizing in background — refresh in 30–90s
                </span>
              ) : (
                <span className="text-zinc-500">
                  · {manifest.synthesized_profiles} profile{manifest.synthesized_profiles === 1 ? '' : 's'} synthesized
                </span>
              )}
            </div>
            {showManifest ? <CaretUp size={14} className="text-zinc-500" /> : <CaretDown size={14} className="text-zinc-500" />}
          </button>
          {showManifest && (
            <div className="px-5 pb-5 border-t border-zinc-800/60 space-y-4">
              {manifest.matched.length > 0 && (
                <div className="mt-3">
                  <h3 className="text-xs uppercase tracking-wider text-emerald-400 mb-2">Matched</h3>
                  <ul className="space-y-1.5">
                    {manifest.matched.map((r, i) => (
                      <li key={i} className="text-xs text-zinc-300 flex items-center gap-2 flex-wrap">
                        <CheckCircle size={11} weight="fill" className="text-emerald-500 shrink-0" />
                        <span className="font-mono text-zinc-500">{r.filename}</span>
                        <span className="text-zinc-600">→</span>
                        <span className="text-zinc-200 font-medium">{r.matched_entity_name}</span>
                        {r.period?.period_label && (
                          <span className="text-zinc-500">· {r.period.period_label}</span>
                        )}
                        <span className="text-zinc-600">· {r.match_method.replace(/_/g, ' ')} · {r.match_confidence}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {manifest.unmatched.length > 0 && (
                <div>
                  <h3 className="text-xs uppercase tracking-wider text-amber-400 mb-2">Unmatched — assign manually</h3>
                  <ul className="space-y-1.5">
                    {manifest.unmatched.map((r, i) => (
                      <li key={i} className="text-xs text-zinc-300 flex items-center gap-2 flex-wrap">
                        <Warning size={11} weight="fill" className="text-amber-500 shrink-0" />
                        <span className="font-mono text-zinc-500">{r.filename}</span>
                        {r.period?.period_label && <span className="text-zinc-500">· {r.period.period_label}</span>}
                        <select
                          onChange={(e) => {
                            const v = parseInt(e.target.value, 10)
                            if (v) handleReassign(r.artifact_id, v)
                          }}
                          defaultValue=""
                          className="ml-auto text-xs bg-zinc-800 border border-zinc-700 text-zinc-200 rounded px-2 py-1"
                        >
                          <option value="">Assign to…</option>
                          {competitors.map(c => (
                            <option key={c.id} value={c.id}>{c.name}</option>
                          ))}
                        </select>
                      </li>
                    ))}
                  </ul>
                  <p className="text-[11px] text-zinc-600 mt-2">
                    Reasoning preserved in artifact metadata. Reassigning re-routes the report to the chosen competitor; re-synthesize their business profile from the competitor detail page if needed.
                  </p>
                </div>
              )}
              {manifest.failed.length > 0 && (
                <div>
                  <h3 className="text-xs uppercase tracking-wider text-red-400 mb-2">Failed</h3>
                  <ul className="space-y-1.5">
                    {manifest.failed.map((r, i) => (
                      <li key={i} className="text-xs text-zinc-400">
                        <span className="font-mono text-zinc-500">{r.filename}</span>
                        <span className="text-zinc-600 mx-1">·</span>
                        <span className="text-red-300">{r.error}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {data?.message && (
        <div className="border border-dashed border-zinc-800 rounded-xl p-8 text-center">
          <p className="text-sm text-zinc-400">{data.message}</p>
          <p className="text-xs text-zinc-600 mt-2">
            Open any competitor and click <span className="text-emerald-400">Upload annual report</span> to start.
          </p>
        </div>
      )}

      {data?.synthesis && (
        <article className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
          <MdLite text={data.synthesis} />
        </article>
      )}
    </div>
  )
}
