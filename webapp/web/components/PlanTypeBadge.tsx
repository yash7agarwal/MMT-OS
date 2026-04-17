'use client'

import {
  Palette,
  ArrowsLeftRight,
  Link as LinkIcon,
  Warning,
  ListChecks,
} from '@phosphor-icons/react'
import type { PlanType } from '@/lib/types'

const LABELS: Record<PlanType, { icon: React.ReactNode; label: string; classes: string }> = {
  feature_flow: {
    icon: <ListChecks size={12} />,
    label: 'Feature flow',
    classes: 'bg-zinc-500/10 text-zinc-300 border-zinc-500/20',
  },
  design_fidelity: {
    icon: <Palette size={12} />,
    label: 'Design fidelity',
    classes: 'bg-purple-500/10 text-purple-300 border-purple-500/20',
  },
  functional_flow: {
    icon: <ArrowsLeftRight size={12} />,
    label: 'Functional flow',
    classes: 'bg-cyan-500/10 text-cyan-300 border-cyan-500/20',
  },
  deeplink_utility: {
    icon: <LinkIcon size={12} />,
    label: 'Deeplink / utility',
    classes: 'bg-amber-500/10 text-amber-300 border-amber-500/20',
  },
  edge_cases: {
    icon: <Warning size={12} />,
    label: 'Edge cases',
    classes: 'bg-red-500/10 text-red-300 border-red-500/20',
  },
}

export function PlanTypeBadge({ type, size = 'sm' }: { type: PlanType; size?: 'sm' | 'md' }) {
  const spec = LABELS[type] || LABELS.feature_flow
  const sizing = size === 'md' ? 'text-sm px-2.5 py-1' : 'text-xs px-2 py-0.5'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border ${spec.classes} ${sizing} whitespace-nowrap font-medium`}
      title={spec.label}
    >
      {spec.icon}
      <span>{spec.label}</span>
    </span>
  )
}
