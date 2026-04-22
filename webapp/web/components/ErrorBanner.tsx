'use client'

import { WarningCircle } from '@phosphor-icons/react'

type Props = {
  message: string
  title?: string
}

export function ErrorBanner({ message, title = 'Error fetching data' }: Props) {
  return (
    <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 flex items-start gap-3">
      <WarningCircle size={20} className="text-red-400 shrink-0 mt-0.5" />
      <div>
        <div className="text-red-400 font-medium text-sm">{title}</div>
        <div className="text-red-300/80 text-xs mt-1 font-mono break-all">{message}</div>
      </div>
    </div>
  )
}
