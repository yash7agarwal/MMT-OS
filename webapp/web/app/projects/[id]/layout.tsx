'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  ArrowLeft,
  House,
  Brain,
  DeviceMobile,
  Binoculars,
  ChatCircle,
  Queue,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { ProjectDetail } from '@/lib/types'

const tabs = [
  { label: 'Overview', href: '', icon: House },
  { label: 'Intelligence', href: '/intelligence', icon: Brain },
  { label: 'UAT', href: '/uat', icon: DeviceMobile },
  { label: 'Competitors', href: '/competitors', icon: Binoculars },
  { label: 'Ask', href: '/ask', icon: ChatCircle },
  { label: 'Backlog', href: '/backlog', icon: Queue },
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
        All projects
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

      {/* Tab bar */}
      <nav className="flex gap-1 border-b border-zinc-800 mb-6 -mx-1 overflow-x-auto">
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
              className={`inline-flex items-center gap-1.5 px-3 py-2.5 text-sm font-medium border-b-2 transition-colors duration-150 whitespace-nowrap ${
                isActive
                  ? 'text-emerald-400 border-emerald-400'
                  : 'text-zinc-500 border-transparent hover:text-zinc-300 hover:border-zinc-700'
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
