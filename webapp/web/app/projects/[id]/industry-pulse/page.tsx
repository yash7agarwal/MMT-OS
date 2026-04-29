'use client'

import { useEffect, useState } from 'react'
import { Pulse, ArrowsClockwise } from '@phosphor-icons/react'
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
