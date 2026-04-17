'use client'

import { useState } from 'react'
import { CheckCircle, Pencil, Trash, MinusCircle } from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { Screen, TestCase } from '@/lib/types'

interface Props {
  testCase: TestCase
  screens: Screen[]
  onUpdated: (c: TestCase) => void
  onDeleted: (id: number) => void
}

export function TestCaseCard({ testCase: c, screens, onUpdated, onDeleted }: Props) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(c.title)
  const [criteria, setCriteria] = useState(c.acceptance_criteria)
  const [busy, setBusy] = useState(false)

  const targetScreen = screens.find((s) => s.id === c.target_screen_id)
  const isApproved = c.status === 'approved'
  const isRemoved = c.status === 'removed'

  const approve = async () => {
    setBusy(true)
    try {
      const updated = await api.updateCase(c.id, { status: 'approved' })
      onUpdated(updated)
    } finally {
      setBusy(false)
    }
  }

  const reject = async () => {
    setBusy(true)
    try {
      const updated = await api.updateCase(c.id, { status: 'removed' })
      onUpdated(updated)
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    setBusy(true)
    try {
      const updated = await api.updateCase(c.id, {
        title,
        acceptance_criteria: criteria,
      })
      onUpdated(updated)
      setEditing(false)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!confirm(`Permanently delete "${c.title}"?`)) return
    await api.deleteCase(c.id)
    onDeleted(c.id)
  }

  return (
    <div
      className={`border rounded-xl p-4 transition-colors duration-200 ${
        isApproved
          ? 'border-green-500/20 bg-green-500/10'
          : isRemoved
          ? 'border-zinc-800 bg-zinc-950 opacity-50'
          : 'border-zinc-800 bg-zinc-900'
      }`}
    >
      <div className="flex items-start gap-4">
        {/* Target screen thumbnail */}
        {targetScreen && (
          <a
            href={api.screenImageUrl(targetScreen.id)}
            target="_blank"
            rel="noreferrer"
            className="shrink-0 w-16 aspect-[9/19.5] bg-zinc-950 border border-zinc-800 rounded-lg overflow-hidden hover:border-zinc-600 transition-colors duration-150"
            title={targetScreen.display_name || targetScreen.name}
          >
            <img
              src={api.screenImageUrl(targetScreen.id)}
              alt=""
              className="w-full h-full object-contain"
              loading="lazy"
            />
          </a>
        )}

        <div className="flex-1 min-w-0">
          {editing ? (
            <div className="space-y-2">
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 text-sm font-medium focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
              />
              <textarea
                value={criteria}
                onChange={(e) => setCriteria(e.target.value)}
                rows={2}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 text-xs focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
              />
              <div className="flex gap-2">
                <button
                  disabled={busy}
                  onClick={save}
                  className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white px-3 py-1 rounded-lg text-xs transition-colors duration-150 active:scale-[0.98]"
                >
                  Save
                </button>
                <button
                  onClick={() => setEditing(false)}
                  className="bg-zinc-800 hover:bg-zinc-700 text-zinc-300 px-3 py-1 rounded-lg text-xs transition-colors duration-150"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="flex items-start justify-between gap-3">
                <h4 className="font-medium text-sm">{c.title}</h4>
                <span className="text-xs text-zinc-600 shrink-0">
                  {isApproved ? (
                    <span className="inline-flex items-center gap-1 text-green-400">
                      <CheckCircle size={12} />
                      approved
                    </span>
                  ) : isRemoved ? 'removed' : 'proposed'}
                </span>
              </div>
              <p className="text-xs text-zinc-400 mt-2 leading-relaxed">
                {c.acceptance_criteria}
              </p>
              {c.navigation_path && c.navigation_path.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1 text-xs text-zinc-600">
                  {c.navigation_path.map((step, i) => (
                    <span key={i} className="flex items-center gap-1">
                      {i === 0 && <span className="text-zinc-700">{step.from_screen}</span>}
                      <span className="text-zinc-700">&rarr;</span>
                      <span className="text-zinc-500">{step.to_screen}</span>
                      <span className="text-zinc-700 italic">({step.trigger})</span>
                    </span>
                  ))}
                </div>
              )}
              {!isRemoved && (
                <div className="flex gap-3 mt-3">
                  {!isApproved && (
                    <button
                      disabled={busy}
                      onClick={approve}
                      className="inline-flex items-center gap-1 text-xs text-emerald-400 hover:text-emerald-300 font-medium transition-colors duration-150"
                    >
                      <CheckCircle size={12} />
                      Approve
                    </button>
                  )}
                  {isApproved && (
                    <button
                      disabled={busy}
                      onClick={() => onUpdated({ ...c, status: 'proposed' })}
                      className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors duration-150"
                    >
                      Unapprove
                    </button>
                  )}
                  <button
                    disabled={busy}
                    onClick={() => setEditing(true)}
                    className="inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-200 transition-colors duration-150"
                  >
                    <Pencil size={12} />
                    Edit
                  </button>
                  <button
                    disabled={busy}
                    onClick={reject}
                    className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-amber-400 transition-colors duration-150"
                  >
                    <MinusCircle size={12} />
                    Remove
                  </button>
                  <button
                    disabled={busy}
                    onClick={remove}
                    className="inline-flex items-center gap-1 text-xs text-zinc-600 hover:text-red-400 transition-colors duration-150"
                  >
                    <Trash size={12} />
                    Delete
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
