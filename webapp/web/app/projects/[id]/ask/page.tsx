'use client'

import { useState } from 'react'
import {
  MagnifyingGlass,
  PaperPlaneTilt,
  Brain,
  ArrowRight,
  Clock,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { QueryResponse } from '@/lib/types'

const EXAMPLE_QUESTIONS = [
  'How does our hotel booking compare to Booking.com?',
  'What are the latest industry trends?',
  'Which competitors have a loyalty program?',
]

export default function AskPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)

  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [response, setResponse] = useState<QueryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const q = question.trim()
    if (!q) return
    setLoading(true)
    setError(null)
    setResponse(null)
    try {
      const res = await api.queryKnowledge(projectId, q)
      setResponse(res)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleChipClick = (q: string) => {
    setQuestion(q)
    setResponse(null)
    setError(null)
  }

  const confidenceColor = (c: number) => {
    if (c >= 0.7) return 'bg-green-500'
    if (c >= 0.4) return 'bg-amber-500'
    return 'bg-red-500'
  }

  const confidenceLabel = (c: number) => {
    if (c >= 0.7) return 'text-green-400'
    if (c >= 0.4) return 'text-amber-400'
    return 'text-red-400'
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-3xl mx-auto px-4 py-12">
        {/* Header */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-2 mb-3">
            <Brain size={28} className="text-emerald-400" />
            <h1 className="text-2xl font-semibold tracking-tight">Ask your Product OS</h1>
          </div>
          <p className="text-sm text-zinc-500">
            Query the knowledge base with natural language
          </p>
        </div>

        {/* Search input */}
        <form onSubmit={handleSubmit} className="mb-8">
          <div className="relative">
            <MagnifyingGlass
              size={20}
              className="absolute left-4 top-1/2 -translate-y-1/2 text-zinc-500"
            />
            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask a question about your product landscape..."
              className="w-full bg-zinc-800 border border-zinc-700 rounded-xl pl-12 pr-14 py-4 text-sm placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-2 focus:ring-emerald-500/20 transition-colors duration-150"
            />
            <button
              type="submit"
              disabled={loading || !question.trim()}
              className="absolute right-2 top-1/2 -translate-y-1/2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white p-2.5 rounded-lg transition-colors duration-150 active:scale-[0.96]"
            >
              <PaperPlaneTilt size={18} weight="fill" />
            </button>
          </div>
        </form>

        {/* Loading state */}
        {loading && (
          <div className="flex items-center justify-center gap-3 py-12">
            <div className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-sm text-zinc-400">Searching knowledge base...</span>
            <div className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse [animation-delay:300ms]" />
          </div>
        )}

        {/* Error state */}
        {error && (
          <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 mb-6">
            <p className="text-sm text-red-400">{error}</p>
          </div>
        )}

        {/* Response display */}
        {response && (
          <div className="space-y-4">
            {/* Answer */}
            <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
              <pre className="whitespace-pre-wrap text-sm text-zinc-200 font-sans leading-relaxed">
                {response.answer}
              </pre>
            </div>

            {/* Confidence + freshness */}
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-2 flex-1 min-w-[200px]">
                <span className="text-xs text-zinc-500 shrink-0">Confidence</span>
                <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${confidenceColor(response.confidence)}`}
                    style={{ width: `${(response.confidence * 100).toFixed(0)}%` }}
                  />
                </div>
                <span className={`text-xs font-medium ${confidenceLabel(response.confidence)}`}>
                  {(response.confidence * 100).toFixed(0)}%
                </span>
              </div>
              <div className="flex items-center gap-1.5 text-xs text-zinc-500">
                <Clock size={14} />
                <span>{response.data_freshness}</span>
              </div>
            </div>

            {/* Sources */}
            {response.sources.length > 0 && (
              <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
                <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500 mb-2">
                  Sources
                </h3>
                <div className="flex flex-wrap gap-2">
                  {response.sources.map((s) => (
                    <span
                      key={s.entity_id}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800 border border-zinc-700 text-zinc-300"
                    >
                      <span className="text-emerald-400">{s.type}</span>
                      <ArrowRight size={10} className="text-zinc-600" />
                      {s.name}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Follow-up questions */}
            {response.follow_up_questions.length > 0 && (
              <div>
                <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500 mb-2">
                  Follow-up questions
                </h3>
                <div className="flex flex-wrap gap-2">
                  {response.follow_up_questions.map((q) => (
                    <button
                      key={q}
                      onClick={() => handleChipClick(q)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-zinc-900 border border-zinc-800 hover:border-emerald-500/40 hover:text-emerald-300 text-zinc-400 transition-colors duration-150"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Empty state — example questions */}
        {!loading && !response && !error && (
          <div className="text-center py-8">
            <p className="text-xs text-zinc-600 uppercase tracking-wider mb-4">
              Try asking
            </p>
            <div className="flex flex-col gap-2 max-w-md mx-auto">
              {EXAMPLE_QUESTIONS.map((q) => (
                <button
                  key={q}
                  onClick={() => handleChipClick(q)}
                  className="text-left px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-800 hover:border-emerald-500/40 text-sm text-zinc-400 hover:text-zinc-200 transition-colors duration-150"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
