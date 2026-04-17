'use client'

import { useState } from 'react'
import { Pencil, Trash } from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { Screen } from '@/lib/types'

interface Props {
  screen: Screen
  onUpdated: (screen: Screen) => void
  onDeleted: (id: number) => void
}

export function ScreenCard({ screen, onUpdated, onDeleted }: Props) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(screen.display_name || screen.name)
  const [purpose, setPurpose] = useState(screen.purpose || '')

  const save = async () => {
    const updated = await api.updateScreen(screen.id, {
      display_name: name,
      purpose,
    })
    onUpdated(updated)
    setEditing(false)
  }

  const remove = async () => {
    if (!confirm(`Delete "${screen.display_name || screen.name}"?`)) return
    await api.deleteScreen(screen.id)
    onDeleted(screen.id)
  }

  return (
    <div className="border border-zinc-800 bg-zinc-900 rounded-xl overflow-hidden hover:border-zinc-700 transition-colors duration-200">
      <div className="aspect-[9/19.5] bg-zinc-950 relative">
        <img
          src={api.screenImageUrl(screen.id)}
          alt={screen.display_name || screen.name}
          className="w-full h-full object-contain"
          loading="lazy"
        />
      </div>
      <div className="p-3">
        {editing ? (
          <div className="space-y-2">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 text-sm font-medium focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
              placeholder="Display name"
            />
            <textarea
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              rows={2}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 text-xs focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
              placeholder="Purpose"
            />
            <div className="flex gap-2">
              <button
                onClick={save}
                className="bg-emerald-600 hover:bg-emerald-500 text-white px-2 py-1 rounded-lg text-xs transition-colors duration-150 active:scale-[0.98]"
              >
                Save
              </button>
              <button
                onClick={() => setEditing(false)}
                className="bg-zinc-800 hover:bg-zinc-700 text-zinc-300 px-2 py-1 rounded-lg text-xs transition-colors duration-150"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <>
            <h4 className="font-medium text-sm mb-1 truncate" title={screen.display_name || screen.name}>
              {screen.display_name || screen.name}
            </h4>
            <p className="text-xs text-zinc-500 font-mono truncate mb-2">
              {screen.name}
            </p>
            {screen.purpose && (
              <p className="text-xs text-zinc-400 line-clamp-2 mb-2 leading-relaxed">{screen.purpose}</p>
            )}
            {screen.elements && screen.elements.length > 0 && (
              <p className="text-xs text-zinc-600">
                {screen.elements.length} interactive element{screen.elements.length !== 1 && 's'}
              </p>
            )}
            <div className="flex gap-3 mt-3">
              <button
                onClick={() => setEditing(true)}
                className="inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-200 transition-colors duration-150"
              >
                <Pencil size={12} />
                Edit
              </button>
              <button
                onClick={remove}
                className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-red-400 transition-colors duration-150"
              >
                <Trash size={12} />
                Delete
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
