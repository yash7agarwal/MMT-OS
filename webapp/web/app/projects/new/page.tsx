'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import Link from 'next/link'
import { ArrowLeft } from '@phosphor-icons/react'
import { api } from '@/lib/api'

export default function NewProjectPage() {
  const router = useRouter()
  const [name, setName] = useState('')
  const [appPackage, setAppPackage] = useState('')
  const [description, setDescription] = useState('')
  const [enableIntelligence, setEnableIntelligence] = useState(true)
  const [industry, setIndustry] = useState('')
  const [competitorsHint, setCompetitorsHint] = useState('')
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
        enable_intelligence: enableIntelligence,
        industry: industry.trim() || undefined,
        competitors_hint: competitorsHint.trim() || undefined,
      })
      if (enableIntelligence) {
        await api.startProductOS(project.id)
      }
      router.push(`/projects/${project.id}`)
    } catch (e: any) {
      setError(e.message)
      setSubmitting(false)
    }
  }

  return (
    <div className="max-w-xl">
      <Link href="/" className="inline-flex items-center gap-1.5 text-zinc-500 hover:text-zinc-300 text-sm mb-4 transition-colors duration-150">
        <ArrowLeft size={14} />
        Back to products
      </Link>
      <h1 className="text-2xl font-semibold tracking-tight mb-2">New product</h1>
      <p className="text-zinc-400 text-sm mb-8">
        Track one product — test it, research competitors, and monitor the industry.
      </p>

      <form onSubmit={handleSubmit} className="space-y-5">
        <div className="space-y-2">
          <label className="block text-xs font-medium uppercase tracking-wider text-zinc-500">Product name *</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. MakeMyTrip"
            required
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2.5 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
          />
        </div>

        <div className="space-y-2">
          <label className="block text-xs font-medium uppercase tracking-wider text-zinc-500">
            App package <span className="normal-case tracking-normal text-zinc-600">(Android only, optional)</span>
          </label>
          <input
            type="text"
            value={appPackage}
            onChange={(e) => setAppPackage(e.target.value)}
            placeholder="e.g. com.makemytrip"
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2.5 font-mono text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
          />
        </div>

        <div className="space-y-2">
          <label className="block text-xs font-medium uppercase tracking-wider text-zinc-500">
            Description <span className="normal-case tracking-normal text-zinc-600">(optional)</span>
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What does this app do? Who uses it?"
            rows={3}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2.5 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
          />
        </div>

        <div className="border border-zinc-800 rounded-lg p-4 space-y-4">
          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={enableIntelligence}
              onChange={(e) => setEnableIntelligence(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-zinc-600 bg-zinc-900 text-emerald-500 focus:ring-emerald-500/20"
            />
            <div>
              <span className="text-sm font-medium text-white">Start research agents automatically</span>
              <p className="text-xs text-zinc-500 mt-0.5">
                Agents will discover competitors and research your industry based on the description above.
              </p>
            </div>
          </label>

          <div className="space-y-2">
            <label className="block text-xs font-medium uppercase tracking-wider text-zinc-500">
              Industry / domain <span className="normal-case tracking-normal text-zinc-600">(optional)</span>
            </label>
            <input
              type="text"
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              placeholder="e.g., Online travel, Fintech, E-commerce"
              className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2.5 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
            />
          </div>

          <div className="space-y-2">
            <label className="block text-xs font-medium uppercase tracking-wider text-zinc-500">
              Known competitors <span className="normal-case tracking-normal text-zinc-600">(optional)</span>
            </label>
            <input
              type="text"
              value={competitorsHint}
              onChange={(e) => setCompetitorsHint(e.target.value)}
              placeholder="e.g., Booking.com, Expedia, Airbnb"
              className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2.5 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
            />
            <p className="text-xs text-zinc-500">Comma-separated. Agents will discover more automatically.</p>
          </div>
        </div>

        {error && (
          <div className="border border-red-500/20 bg-red-500/10 text-red-200 p-3 rounded-xl text-sm">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !name.trim()}
          className="bg-emerald-600 hover:bg-emerald-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white px-5 py-2 rounded-lg font-medium text-sm transition-colors duration-150 active:scale-[0.98] active:translate-y-[1px]"
        >
          {submitting ? 'Creating...' : 'Create product'}
        </button>
      </form>
    </div>
  )
}
