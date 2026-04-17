'use client'

import { useState } from 'react'
import { Lightning, CheckCircle } from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { FlowInferenceResult, InferredEdge, Screen } from '@/lib/types'

interface Props {
  projectId: number
  screens: Screen[]
  onEdgesAccepted: () => void
}

export function FlowInferencePanel({ projectId, screens, onEdgesAccepted }: Props) {
  const [result, setResult] = useState<FlowInferenceResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [accepted, setAccepted] = useState<Set<number>>(new Set())

  const screenName = (id: number) => {
    const s = screens.find((x) => x.id === id)
    return s?.display_name || s?.name || `screen ${id}`
  }

  const runInference = async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await api.inferFlow(projectId)
      setResult(r)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const acceptEdge = async (e: InferredEdge, idx: number) => {
    await api.createEdge(projectId, {
      from_screen_id: e.from_screen_id,
      to_screen_id: e.to_screen_id,
      trigger: e.trigger,
    })
    setAccepted((prev) => new Set(prev).add(idx))
    onEdgesAccepted()
  }

  const acceptAll = async () => {
    if (!result) return
    for (let i = 0; i < result.proposed_edges.length; i++) {
      if (!accepted.has(i)) {
        await acceptEdge(result.proposed_edges[i], i)
      }
    }
  }

  if (screens.length < 2) {
    return null
  }

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="font-medium text-base">Infer the navigation flow</h3>
          <p className="text-sm text-zinc-400 mt-1">
            Claude will analyze your {screens.length} screens and propose how they connect.
          </p>
        </div>
        <button
          onClick={runInference}
          disabled={loading}
          className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white px-4 py-2 rounded-lg font-medium transition-colors duration-150 text-sm active:scale-[0.98] active:translate-y-[1px]"
        >
          <Lightning size={14} weight="fill" />
          {loading ? 'Analyzing...' : result ? 'Re-run inference' : 'Infer flow'}
        </button>
      </div>

      {error && (
        <div className="border border-red-500/20 bg-red-500/10 text-red-200 p-3 rounded-xl text-sm mt-3">
          {error}
        </div>
      )}

      {result && (
        <div className="mt-5 space-y-4">
          {result.home_screen_id && (
            <div className="text-sm">
              <span className="text-zinc-500">Detected home screen:</span>{' '}
              <span className="text-emerald-400 font-medium">{screenName(result.home_screen_id)}</span>
            </div>
          )}

          {result.branches.length > 0 && (
            <div>
              <p className="text-sm text-zinc-500 mb-2">Detected branches:</p>
              <div className="space-y-1">
                {result.branches.map((b, i) => (
                  <div key={i} className="text-sm bg-zinc-950/60 border border-zinc-800 rounded-lg p-2">
                    <span className="text-amber-400 font-medium">{b.name}</span>
                    <span className="text-zinc-500 ml-2">
                      ({b.screen_ids.map(screenName).join(' vs ')})
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {result.proposed_edges.length > 0 ? (
            <div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm text-zinc-500">
                  {result.proposed_edges.length} proposed edge{result.proposed_edges.length !== 1 && 's'}
                </p>
                <button
                  onClick={acceptAll}
                  className="text-xs text-emerald-400 hover:text-emerald-300 transition-colors duration-150"
                >
                  Accept all
                </button>
              </div>
              <div className="space-y-2">
                {result.proposed_edges.map((e, i) => {
                  const isAccepted = accepted.has(i)
                  return (
                    <div
                      key={i}
                      className={`border rounded-xl p-3 text-sm ${
                        isAccepted
                          ? 'border-green-500/20 bg-green-500/10'
                          : 'border-zinc-800 bg-zinc-900'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-medium truncate">{screenName(e.from_screen_id)}</span>
                            <span className="text-zinc-600">&rarr;</span>
                            <span className="font-medium truncate">{screenName(e.to_screen_id)}</span>
                          </div>
                          <p className="text-xs text-zinc-500 mt-1">
                            <span className="text-zinc-400">{e.trigger}</span>
                            <span className="ml-2">confidence {(e.confidence * 100).toFixed(0)}%</span>
                          </p>
                          <p className="text-xs text-zinc-600 mt-1 italic">{e.reasoning}</p>
                        </div>
                        {isAccepted ? (
                          <span className="inline-flex items-center gap-1 text-emerald-400 text-xs font-medium">
                            <CheckCircle size={12} />
                            Added
                          </span>
                        ) : (
                          <button
                            onClick={() => acceptEdge(e, i)}
                            className="text-xs bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 hover:border-zinc-600 text-zinc-200 px-3 py-1 rounded-lg transition-colors duration-150"
                          >
                            Accept
                          </button>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          ) : (
            <p className="text-sm text-zinc-500">
              Claude couldn&apos;t propose any edges from this set. Try uploading more screens or connect them manually below.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
