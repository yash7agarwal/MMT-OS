'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  ArrowLeft,
  House,
  Brain,
  Binoculars,
  ChatCircle,
  FileText,
  Funnel,
  GitBranch,
  TrendUp,
  Pulse,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { ProjectDetail } from '@/lib/types'

const tabs = [
  { label: 'Overview', href: '', icon: House },
  { label: 'Competitors', href: '/competitors', icon: Binoculars },
  { label: 'Lenses', href: '/lenses', icon: Funnel },
  { label: 'Trends', href: '/trends', icon: TrendUp },
  { label: 'Impacts', href: '/impacts', icon: GitBranch },
  { label: 'Industry Pulse', href: '/industry-pulse', icon: Pulse },
  { label: 'Ask', href: '/ask', icon: ChatCircle },
  { label: 'Intelligence', href: '/intelligence', icon: Brain },
  { label: 'Reports', href: '/reports', icon: FileText },
]

export default function ProjectLayout({
  params,
  children,
}: {
  params: { id: string }
  children: React.ReactNode
}) {
  const projectId = parseInt(params.id, 10)
  const pathname = usePathname()
  const [project, setProject] = useState<ProjectDetail | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api
      .getProject(projectId)
      .then(setProject)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [projectId])

  const basePath = `/projects/${projectId}`

  return (
    <div>
      {/* Back link */}
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-zinc-500 hover:text-zinc-300 text-sm mb-4 transition-colors duration-150"
      >
        <ArrowLeft size={14} />
        All products
      </Link>

      {/* Project header */}
      {loading ? (
        <div className="mb-6 space-y-2">
          <div className="skeleton h-7 w-48" />
          <div className="skeleton h-4 w-32" />
          <div className="skeleton h-4 w-64" />
        </div>
      ) : project ? (
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
          {project.app_package && (
            <p className="text-zinc-500 font-mono text-sm mt-1">{project.app_package}</p>
          )}
          {project.description && (
            <p className="text-zinc-400 mt-2 max-w-2xl text-sm line-clamp-2">
              {project.description}
            </p>
          )}
        </div>
      ) : null}

      <div className="h-px w-full bg-gradient-to-r from-transparent via-emerald-500/20 to-transparent mb-4" />

      {/* Tab bar */}
      <nav className="flex gap-1 mb-6 p-1 bg-zinc-900/50 rounded-xl border border-zinc-800/50">
        {tabs.map((tab) => {
          const tabPath = `${basePath}${tab.href}`
          const isActive =
            tab.href === ''
              ? pathname === basePath || pathname === `${basePath}/`
              : pathname.startsWith(tabPath)
          const Icon = tab.icon

          return (
            <Link
              key={tab.label}
              href={tabPath}
              className={`inline-flex items-center gap-1.5 px-3 py-2.5 text-sm font-medium rounded-lg transition-colors duration-150 whitespace-nowrap ${
                isActive
                  ? 'bg-emerald-500/10 text-emerald-400'
                  : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
              }`}
            >
              <Icon size={16} weight={isActive ? 'fill' : 'regular'} />
              {tab.label}
            </Link>
          )
        })}
      </nav>

      {/* Page content */}
      {children}
    </div>
  )
}
