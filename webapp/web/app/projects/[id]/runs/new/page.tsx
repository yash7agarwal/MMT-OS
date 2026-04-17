'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Play, Warning, CheckCircle, Info } from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { FigmaImportSummary } from '@/lib/types'

export default function NewUatRunPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const router = useRouter()
  const [apkPath, setApkPath] = useState('.tmp/builds/candidate.apk')
  const [figmaFileId, setFigmaFileId] = useState('rid4WC0zcs0yt3RjpST0dx')
  const [featureDescription, setFeatureDescription] = useState('hotel details page')
  const [skipInstall, setSkipInstall] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<string | null>(null)
  const [imports, setImports] = useState<FigmaImportSummary[]>([])

  useEffect(() => {
    api.listFigmaImports(projectId).then(setImports).catch(() => {})
  }, [projectId])

  const readyImports = imports.filter((i) => i.status === 'ready')
  const matchingImport = readyImports.find((i) => i.figma_file_id === figmaFileId.trim())

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!figmaFileId.trim()) return
    setSubmitting(true)
    setError(null)
    setProgress('Starting run -- this takes 60-300s depending on frame count. Please keep this tab open.')

    try {
      const run = await api.createUatRun(projectId, {
        apk_path: apkPath.trim() || null,
        figma_file_id: figmaFileId.trim(),
        feature_description: featureDescription.trim() || null,
        skip_install: skipInstall,
      })
      router.push(`/projects/${projectId}/runs/${run.id}`)
    } catch (e: any) {
      setError(e.message)
      setSubmitting(false)
      setProgress(null)
    }
  }

  return (
    <div className="max-w-xl">
      <Link
        href={`/projects/${projectId}/runs`}
        className="inline-flex items-center gap-1.5 text-zinc-500 hover:text-zinc-300 text-sm mb-4 transition-colors duration-150"
      >
        <ArrowLeft size={14} />
        Back to runs
      </Link>
      <h1 className="text-2xl font-semibold tracking-tight mb-2">Start UAT run</h1>
      <p className="text-zinc-400 text-sm mb-8">
        The system will install the APK, autonomously navigate through every Figma frame, and produce a comparison report.
      </p>

      {/* Figma import status banner */}
      {figmaFileId.trim() && !matchingImport && (
        <div className="mb-5 border border-amber-500/20 bg-amber-500/10 text-amber-200 p-3 rounded-xl text-sm flex items-start gap-2">
          <Warning size={16} className="text-amber-400 mt-0.5 shrink-0" />
          <div>
            No ready Figma import for <code className="text-amber-300 font-mono">{figmaFileId}</code> in this project.
            <div className="mt-1">
              Go back to the project page and import this Figma file once before starting a run.
              {' '}
              <Link
                href={`/projects/${projectId}`}
                className="underline hover:text-amber-100 transition-colors duration-150"
              >
                Open project
              </Link>
            </div>
          </div>
        </div>
      )}
      {matchingImport && (
        <div className="mb-5 border border-green-500/20 bg-green-500/10 text-emerald-300 p-3 rounded-xl text-sm flex items-start gap-2">
          <CheckCircle size={16} className="text-green-400 mt-0.5 shrink-0" />
          <span>Using Figma import #{matchingImport.id} &middot; &quot;{matchingImport.file_name}&quot; &middot; {matchingImport.total_frames} frames</span>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-5">
        <div className="space-y-2">
          <label className="block text-xs font-medium uppercase tracking-wider text-zinc-500">
            APK path <span className="normal-case tracking-normal text-zinc-600">(relative to repo root)</span>
          </label>
          <input
            type="text"
            value={apkPath}
            onChange={(e) => setApkPath(e.target.value)}
            placeholder=".tmp/builds/candidate.apk"
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2.5 text-sm font-mono text-white placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
          />
          <label className="flex items-center gap-2 mt-2 text-sm text-zinc-400">
            <input
              type="checkbox"
              checked={skipInstall}
              onChange={(e) => setSkipInstall(e.target.checked)}
              className="accent-emerald-500"
            />
            Skip install -- use APK already on device
          </label>
        </div>

        <div className="space-y-2">
          <label className="block text-xs font-medium uppercase tracking-wider text-zinc-500">Figma file ID *</label>
          <input
            type="text"
            value={figmaFileId}
            onChange={(e) => setFigmaFileId(e.target.value)}
            placeholder="e.g. rid4WC0zcs0yt3RjpST0dx"
            required
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2.5 text-sm font-mono text-white placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
          />
          <p className="text-xs text-zinc-600 mt-1">
            Find in the Figma URL: figma.com/design/<strong>&lt;file_id&gt;</strong>/...
          </p>
        </div>

        <div className="space-y-2">
          <label className="block text-xs font-medium uppercase tracking-wider text-zinc-500">
            Feature description <span className="normal-case tracking-normal text-zinc-600">(optional)</span>
          </label>
          <textarea
            value={featureDescription}
            onChange={(e) => setFeatureDescription(e.target.value)}
            placeholder="e.g. hotel details page with new design"
            rows={2}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2.5 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-colors duration-150"
          />
        </div>

        {progress && !error && (
          <div className="border border-cyan-500/20 bg-cyan-500/10 text-cyan-200 p-3 rounded-xl text-sm flex items-start gap-2">
            <Info size={16} className="text-cyan-400 mt-0.5 shrink-0" />
            <span>{progress}</span>
          </div>
        )}
        {error && (
          <div className="border border-red-500/20 bg-red-500/10 text-red-200 p-3 rounded-xl text-sm">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !figmaFileId.trim() || !matchingImport}
          className="w-full inline-flex items-center justify-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white px-5 py-3 rounded-lg font-semibold text-sm transition-colors duration-150 active:scale-[0.98] active:translate-y-[1px]"
        >
          <Play size={16} weight="fill" />
          {submitting
            ? 'Running UAT...'
            : !matchingImport
            ? 'Import Figma file first'
            : 'Start UAT run'}
        </button>
      </form>

      <div className="mt-8 text-xs text-zinc-600 space-y-1">
        <p>Prerequisites:</p>
        <p>- Android device connected via USB with <code className="text-zinc-500 font-mono">adb devices</code> showing online</p>
        <p>- <code className="text-zinc-500 font-mono">FIGMA_ACCESS_TOKEN</code> set in <code className="text-zinc-500 font-mono">.env</code></p>
        <p>- APK file present at the path specified above (if not using skip-install)</p>
      </div>
    </div>
  )
}
