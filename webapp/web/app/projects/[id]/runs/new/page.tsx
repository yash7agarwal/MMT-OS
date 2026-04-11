'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import Link from 'next/link'
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
    setProgress('Starting run — this takes 60-300s depending on frame count. Please keep this tab open.')

    try {
      const run = await api.createUatRun(projectId, {
        apk_path: apkPath.trim() || null,
        figma_file_id: figmaFileId.trim(),
        feature_description: featureDescription.trim() || null,
        skip_install: skipInstall,
      })
      // Navigate to the run detail page
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
        className="text-zinc-500 hover:text-zinc-300 text-sm mb-4 inline-block"
      >
        ← Back to runs
      </Link>
      <h1 className="text-3xl font-bold mb-2">Start UAT run</h1>
      <p className="text-zinc-400 mb-8">
        The system will install the APK, autonomously navigate through every Figma frame, and produce a comparison report.
      </p>

      {/* Figma import status banner */}
      {figmaFileId.trim() && !matchingImport && (
        <div className="mb-5 border border-amber-900 bg-amber-950/30 text-amber-200 p-3 rounded-md text-sm">
          ⚠️ No ready Figma import for <code className="text-amber-300">{figmaFileId}</code> in this project.
          <div className="mt-1">
            Go back to the project page and import this Figma file once before starting a run.
            {' '}
            <Link
              href={`/projects/${projectId}`}
              className="underline hover:text-amber-100"
            >
              Open project →
            </Link>
          </div>
        </div>
      )}
      {matchingImport && (
        <div className="mb-5 border border-emerald-900 bg-emerald-950/20 text-emerald-300 p-3 rounded-md text-sm">
          ✓ Using Figma import #{matchingImport.id} · &quot;{matchingImport.file_name}&quot; · {matchingImport.total_frames} frames
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-5">
        <div>
          <label className="block text-sm font-medium mb-2">
            APK path <span className="text-zinc-500 text-xs">(relative to repo root)</span>
          </label>
          <input
            type="text"
            value={apkPath}
            onChange={(e) => setApkPath(e.target.value)}
            placeholder=".tmp/builds/candidate.apk"
            className="w-full bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 text-sm font-mono focus:outline-none focus:border-indigo-500"
          />
          <label className="flex items-center gap-2 mt-2 text-sm text-zinc-400">
            <input
              type="checkbox"
              checked={skipInstall}
              onChange={(e) => setSkipInstall(e.target.checked)}
            />
            Skip install — use APK already on device
          </label>
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">Figma file ID *</label>
          <input
            type="text"
            value={figmaFileId}
            onChange={(e) => setFigmaFileId(e.target.value)}
            placeholder="e.g. rid4WC0zcs0yt3RjpST0dx"
            required
            className="w-full bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 text-sm font-mono focus:outline-none focus:border-indigo-500"
          />
          <p className="text-xs text-zinc-600 mt-1">
            Find in the Figma URL: figma.com/design/<strong>&lt;file_id&gt;</strong>/...
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">
            Feature description <span className="text-zinc-500 text-xs">(optional)</span>
          </label>
          <textarea
            value={featureDescription}
            onChange={(e) => setFeatureDescription(e.target.value)}
            placeholder="e.g. hotel details page with new design"
            rows={2}
            className="w-full bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-indigo-500"
          />
        </div>

        {progress && !error && (
          <div className="border border-blue-900 bg-blue-950/30 text-blue-200 p-3 rounded-md text-sm">
            {progress}
          </div>
        )}
        {error && (
          <div className="border border-red-900 bg-red-950 text-red-200 p-3 rounded-md text-sm">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !figmaFileId.trim() || !matchingImport}
          className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white px-5 py-3 rounded-md font-semibold transition"
        >
          {submitting
            ? '▶ Running UAT…'
            : !matchingImport
            ? '⚠ Import Figma file first'
            : '▶ Start UAT run'}
        </button>
      </form>

      <div className="mt-8 text-xs text-zinc-600 space-y-1">
        <p>Prerequisites:</p>
        <p>• Android device connected via USB with <code className="text-zinc-500">adb devices</code> showing online</p>
        <p>• <code className="text-zinc-500">FIGMA_ACCESS_TOKEN</code> set in <code className="text-zinc-500">.env</code></p>
        <p>• APK file present at the path specified above (if not using skip-install)</p>
      </div>
    </div>
  )
}
