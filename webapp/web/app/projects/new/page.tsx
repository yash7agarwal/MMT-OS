'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/api'

export default function NewProjectPage() {
  const router = useRouter()
  const [name, setName] = useState('')
  const [appPackage, setAppPackage] = useState('')
  const [description, setDescription] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      const project = await api.createProject({
        name: name.trim(),
        app_package: appPackage.trim() || undefined,
        description: description.trim() || undefined,
      })
      router.push(`/projects/${project.id}`)
    } catch (e: any) {
      setError(e.message)
      setSubmitting(false)
    }
  }

  return (
    <div className="max-w-xl">
      <Link href="/" className="text-zinc-500 hover:text-zinc-300 text-sm mb-4 inline-block">
        ← Back to projects
      </Link>
      <h1 className="text-3xl font-bold mb-2">New project</h1>
      <p className="text-zinc-400 mb-8">
        A project represents one app you want to map and UAT.
      </p>

      <form onSubmit={handleSubmit} className="space-y-5">
        <div>
          <label className="block text-sm font-medium mb-2">Project name *</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. MakeMyTrip"
            required
            className="w-full bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 focus:outline-none focus:border-indigo-500"
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">
            App package <span className="text-zinc-500 text-xs">(Android only, optional)</span>
          </label>
          <input
            type="text"
            value={appPackage}
            onChange={(e) => setAppPackage(e.target.value)}
            placeholder="e.g. com.makemytrip"
            className="w-full bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 font-mono text-sm focus:outline-none focus:border-indigo-500"
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">
            Description <span className="text-zinc-500 text-xs">(optional)</span>
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What does this app do? Who uses it?"
            rows={3}
            className="w-full bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 focus:outline-none focus:border-indigo-500"
          />
        </div>

        {error && (
          <div className="border border-red-900 bg-red-950 text-red-200 p-3 rounded-md text-sm">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !name.trim()}
          className="bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white px-5 py-2 rounded-md font-medium transition"
        >
          {submitting ? 'Creating…' : 'Create project'}
        </button>
      </form>
    </div>
  )
}
