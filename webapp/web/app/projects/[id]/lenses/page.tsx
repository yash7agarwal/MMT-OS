'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  Palette,
  TrendUp,
  Package,
  CurrencyDollar,
  Cpu,
  ShieldCheck,
  CastleTurret,
  Compass,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import { ErrorBanner } from '@/components/ErrorBanner'

const LENS_META: Record<string, { icon: typeof Palette; label: string; question: string }> = {
  product_craft: { icon: Palette, label: 'Product Craft', question: 'How good is their product execution?' },
  growth: { icon: TrendUp, label: 'Growth', question: 'How are they growing?' },
  supply: { icon: Package, label: 'Supply', question: 'What supply advantage do they have?' },
  monetization: { icon: CurrencyDollar, label: 'Monetization', question: 'How do they make money?' },
  technology: { icon: Cpu, label: 'Technology', question: "What's their tech edge?" },
  brand_trust: { icon: ShieldCheck, label: 'Brand & Trust', question: 'How strong is their brand?' },
  moat: { icon: CastleTurret, label: 'Moat', question: "What's defensible?" },
  trajectory: { icon: Compass, label: 'Trajectory', question: 'Where are they headed?' },
}

type Competitor = {
  id: number
  name: string
  lens_counts: Record<string, number>
  total_observations: number
}

type LensMatrixData = {
  lenses: string[]
  competitors: Competitor[]
}

export default function LensMatrixPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [data, setData] = useState<LensMatrixData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setError(null)
    api
      .lensMatrix(projectId)
      .then(setData)
      .catch((err: Error) => setError(err.message || String(err)))
      .finally(() => setLoading(false))
  }, [projectId])

  const hasData =
    data &&
    data.competitors.some((c) =>
      Object.values(c.lens_counts).some((v) => v > 0)
    )

  function cellBg(count: number): string {
    if (count === 0) return 'bg-zinc-900'
    if (count <= 2) return 'bg-emerald-500/5'
    return 'bg-emerald-500/10'
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-8 w-64" />
        <div className="skeleton h-4 w-96" />
        <div className="skeleton h-64 w-full rounded-xl" />
      </div>
    )
  }

  return (
    <div>
      <h2 className="text-xl font-semibold tracking-tight">Strategic Lenses</h2>
      <p className="text-zinc-500 text-sm mt-1 mb-6">
        Analyze competitors through different strategic dimensions
      </p>

      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      {!hasData ? (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-12 text-center">
          <Compass size={40} className="mx-auto text-zinc-600 mb-3" />
          <p className="text-zinc-400 text-sm max-w-md mx-auto">
            No lens-tagged findings yet. Run the competitive intel agent — findings
            will be auto-tagged with strategic lenses.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-zinc-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800">
                <th className="text-left px-4 py-3 text-zinc-400 font-medium w-56">
                  Lens
                </th>
                {data!.competitors.map((c) => (
                  <th
                    key={c.id}
                    className="text-center px-4 py-3 text-zinc-400 font-medium min-w-[120px]"
                  >
                    {c.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data!.lenses.map((lens) => {
                const meta = LENS_META[lens]
                if (!meta) return null
                const Icon = meta.icon

                return (
                  <tr key={lens} className="border-b border-zinc-800/50 last:border-0">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2.5">
                        <Icon size={18} className="text-emerald-400 shrink-0" />
                        <div>
                          <div className="text-zinc-200 font-medium">{meta.label}</div>
                          <div className="text-zinc-500 text-xs mt-0.5">{meta.question}</div>
                        </div>
                      </div>
                    </td>
                    {data!.competitors.map((c) => {
                      const count = c.lens_counts[lens] ?? 0
                      return (
                        <td key={c.id} className="px-4 py-3 text-center">
                          <Link
                            href={`/projects/${projectId}/lenses/${lens}`}
                            className={`inline-flex items-center justify-center w-10 h-10 rounded-lg ${cellBg(count)} hover:ring-1 hover:ring-emerald-500/30 transition-all duration-150`}
                          >
                            {count > 0 ? (
                              <span className="text-emerald-400 font-medium">{count}</span>
                            ) : (
                              <span className="text-zinc-700">—</span>
                            )}
                          </Link>
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
