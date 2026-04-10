'use client'

import { useCallback, useState } from 'react'
import { api } from '@/lib/api'
import type { Screen } from '@/lib/types'

interface Props {
  projectId: number
  onUploaded: (screens: Screen[]) => void
}

export function ScreenUploader({ projectId, onUploaded }: Props) {
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleFiles = useCallback(
    async (files: FileList | File[]) => {
      const fileArray = Array.from(files).filter((f) => f.type.startsWith('image/'))
      if (fileArray.length === 0) {
        setError('No image files detected. Drop PNG/JPG screenshots.')
        return
      }
      setError(null)
      setUploading(true)
      setProgress({ done: 0, total: fileArray.length })
      try {
        const screens = await api.uploadScreensBulk(projectId, fileArray)
        setProgress({ done: fileArray.length, total: fileArray.length })
        onUploaded(screens)
      } catch (e: any) {
        setError(e.message)
      } finally {
        setUploading(false)
        setTimeout(() => setProgress(null), 2000)
      }
    },
    [projectId, onUploaded]
  )

  return (
    <div>
      <div
        onDragEnter={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={(e) => {
          e.preventDefault()
          setDragging(false)
        }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          handleFiles(e.dataTransfer.files)
        }}
        className={`border-2 border-dashed rounded-lg p-10 text-center transition ${
          dragging
            ? 'border-indigo-500 bg-indigo-950/30'
            : 'border-zinc-700 hover:border-zinc-600'
        }`}
      >
        <div className="text-4xl mb-3">📱</div>
        <p className="text-zinc-300 font-medium mb-1">
          Drop your screenshots here
        </p>
        <p className="text-zinc-500 text-sm mb-4">
          Drag & drop multiple PNG/JPG files at once. They can be in any order — Claude will analyze each and infer the flow.
        </p>
        <label className="inline-block bg-zinc-800 hover:bg-zinc-700 text-zinc-200 px-4 py-2 rounded-md text-sm cursor-pointer transition">
          Or browse files
          <input
            type="file"
            multiple
            accept="image/*"
            className="hidden"
            onChange={(e) => e.target.files && handleFiles(e.target.files)}
          />
        </label>
      </div>

      {progress && (
        <div className="mt-3 text-sm text-zinc-400">
          {uploading ? (
            <>
              Analyzing {progress.total} screenshot{progress.total !== 1 && 's'} with Claude vision…
              <span className="text-zinc-600 ml-2">(~2-3s per screen)</span>
            </>
          ) : (
            <span className="text-emerald-400">
              ✓ Uploaded and analyzed {progress.done} screen{progress.done !== 1 && 's'}
            </span>
          )}
        </div>
      )}

      {error && (
        <div className="mt-3 border border-red-900 bg-red-950 text-red-200 p-3 rounded-md text-sm">
          {error}
        </div>
      )}
    </div>
  )
}
