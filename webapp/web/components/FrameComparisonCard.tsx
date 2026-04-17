'use client'

import { CheckCircle, Warning, XCircle } from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { UatFrameResult, UatVerdict } from '@/lib/types'

const VERDICT_STYLES: Record<UatVerdict, { label: string; icon: React.ReactNode; classes: string }> = {
  MATCHES:     { label: 'Matches',     icon: <CheckCircle size={12} />, classes: 'bg-green-500/10 text-green-400 border border-green-500/20' },
  DIFFERS:     { label: 'Differs',     icon: <Warning size={12} />,     classes: 'bg-amber-500/10 text-amber-400 border border-amber-500/20' },
  UNREACHABLE: { label: 'Unreachable', icon: <XCircle size={12} />,     classes: 'bg-zinc-500/10 text-zinc-500 border border-zinc-500/20' },
  ERROR:       { label: 'Error',       icon: <Warning size={12} />,     classes: 'bg-red-500/10 text-red-400 border border-red-500/20' },
}

interface Props {
  runId: number
  frame: UatFrameResult
}

export function FrameComparisonCard({ runId, frame }: Props) {
  const style = VERDICT_STYLES[frame.verdict] || VERDICT_STYLES.ERROR
  const scorePct = frame.match_score !== null ? `${(frame.match_score * 100).toFixed(0)}%` : '--'

  return (
    <div className="border border-zinc-800 bg-zinc-900 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-zinc-800 flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="font-medium text-sm truncate">{frame.figma_frame_name}</h3>
          <p className="text-xs text-zinc-600 font-mono mt-0.5">{frame.figma_node_id}</p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-sm font-mono text-zinc-400">{scorePct}</span>
          <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${style.classes}`}>
            {style.icon}
            {style.label}
          </span>
        </div>
      </div>

      {/* Image row */}
      {frame.verdict !== 'UNREACHABLE' && (
        <div className="p-4 grid grid-cols-1 md:grid-cols-3 gap-3">
          <ImageBox
            label="Figma (design)"
            src={frame.figma_image_path ? api.uatFigmaImageUrl(runId, frame.id) : null}
          />
          <ImageBox
            label="App (actual)"
            src={frame.app_screenshot_path ? api.uatAppScreenshotUrl(runId, frame.id) : null}
          />
          <ImageBox
            label="Diff"
            src={frame.diff_image_path ? api.uatDiffImageUrl(runId, frame.id) : null}
          />
        </div>
      )}

      {/* Issues */}
      {frame.issues && frame.issues.length > 0 && (
        <div className="px-4 pb-4">
          <p className="text-xs text-zinc-500 mb-2 font-medium uppercase tracking-wider">
            Issues ({frame.issues.length})
          </p>
          <ul className="space-y-1">
            {frame.issues.map((issue, i) => (
              <li key={i} className="text-sm text-zinc-300 pl-4 relative leading-relaxed">
                <span className="absolute left-0 text-zinc-600">&bull;</span>
                {issue}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Footer meta */}
      <div className="px-4 py-2 border-t border-zinc-800 text-xs text-zinc-600 flex gap-4">
        <span>nav steps: {frame.navigation_steps}</span>
        {frame.elapsed_s !== null && <span>{frame.elapsed_s.toFixed(1)}s</span>}
      </div>
    </div>
  )
}

function ImageBox({ label, src }: { label: string; src: string | null }) {
  return (
    <div>
      <p className="text-xs text-zinc-500 mb-1.5 font-medium">{label}</p>
      <div className="aspect-[9/19.5] bg-zinc-950 border border-zinc-800 rounded-lg overflow-hidden">
        {src ? (
          <a href={src} target="_blank" rel="noreferrer" className="block w-full h-full">
            <img src={src} alt={label} className="w-full h-full object-contain" loading="lazy" />
          </a>
        ) : (
          <div className="w-full h-full flex items-center justify-center text-xs text-zinc-700">
            not available
          </div>
        )}
      </div>
    </div>
  )
}
