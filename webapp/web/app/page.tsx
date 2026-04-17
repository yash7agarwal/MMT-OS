'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Folder, Plus } from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { Project } from '@/lib/types'

export default function HomePage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .listProjects()
      .then(setProjects)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Your products</h1>
          <p className="text-zinc-400 mt-1 text-sm">
            Each product tracks one app — UAT, competitive research, and market intelligence in one place.
          </p>
        </div>
        <Link
          href="/projects/new"
          className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg font-medium text-sm transition-colors duration-150 active:scale-[0.98] active:translate-y-[1px]"
        >
          <Plus size={16} weight="bold" />
          New product
        </Link>
      </div>

      {loading && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[0, 1].map((i) => (
            <div key={i} className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
              <div className="skeleton h-5 w-32 mb-3" />
              <div className="skeleton h-3 w-20 mb-3" />
              <div className="skeleton h-4 w-full mb-2" />
              <div className="skeleton h-3 w-24 mt-3" />
            </div>
          ))}
        </div>
      )}
      {error && (
        <div className="border border-red-500/20 bg-red-500/10 text-red-200 p-4 rounded-xl">
          Error: {error}
          <p className="text-xs mt-2 text-red-300">
            Make sure the backend is running:{' '}
            <code className="font-mono text-zinc-400">.venv/bin/python3 -m uvicorn webapp.api.main:app --reload --port 8000</code>
          </p>
        </div>
      )}

      {!loading && !error && projects.length === 0 && (
        <div className="border border-dashed border-zinc-800 rounded-xl p-12 text-center">
          <Folder size={32} className="text-zinc-600 mx-auto mb-3" />
          <p className="text-zinc-400 text-sm mb-4">No products yet.</p>
          <Link
            href="/projects/new"
            className="text-emerald-400 hover:text-emerald-300 font-medium text-sm"
          >
            Add your first product
          </Link>
        </div>
      )}

      {projects.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {projects.map((p, index) => (
            <Link
              key={p.id}
              href={`/projects/${p.id}`}
              className="bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-xl p-5 transition-colors duration-200 animate-fade-in-up"
              style={{ animationDelay: `${index * 80}ms` } as React.CSSProperties}
            >
              <h3 className="font-medium text-base mb-1">{p.name}</h3>
              {p.app_package && (
                <p className="text-xs text-zinc-500 font-mono mb-2">{p.app_package}</p>
              )}
              {p.description && (
                <p className="text-sm text-zinc-400 line-clamp-2">{p.description}</p>
              )}
              <p className="text-xs text-zinc-600 mt-3">
                Created {new Date(p.created_at).toLocaleDateString()}
              </p>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
