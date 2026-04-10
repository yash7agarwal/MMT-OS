'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
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
          <h1 className="text-3xl font-bold">Your projects</h1>
          <p className="text-zinc-400 mt-1">
            Each project is one app you want to map and UAT.
          </p>
        </div>
        <Link
          href="/projects/new"
          className="bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-md font-medium transition"
        >
          + New project
        </Link>
      </div>

      {loading && <p className="text-zinc-500">Loading…</p>}
      {error && (
        <div className="border border-red-900 bg-red-950 text-red-200 p-4 rounded-md">
          Error: {error}
          <p className="text-xs mt-2 text-red-300">
            Make sure the backend is running:{' '}
            <code>.venv/bin/python3 -m uvicorn webapp.api.main:app --reload --port 8000</code>
          </p>
        </div>
      )}

      {!loading && !error && projects.length === 0 && (
        <div className="border border-dashed border-zinc-700 rounded-lg p-12 text-center">
          <p className="text-zinc-400 mb-4">No projects yet.</p>
          <Link
            href="/projects/new"
            className="text-indigo-400 hover:text-indigo-300 font-medium"
          >
            Create your first project →
          </Link>
        </div>
      )}

      {projects.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects.map((p) => (
            <Link
              key={p.id}
              href={`/projects/${p.id}`}
              className="border border-zinc-800 bg-zinc-900/50 hover:border-zinc-700 hover:bg-zinc-900 rounded-lg p-5 transition"
            >
              <h3 className="font-semibold text-lg mb-1">{p.name}</h3>
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
