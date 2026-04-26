// Typed fetch client for the FastAPI backend.
// All requests go through Next.js rewrite proxy → http://localhost:8000

import type {
  AgentSession,
  Edge,
  FlowInferenceResult,
  InferredEdge,
  KnowledgeArtifact,
  KnowledgeEntity,
  KnowledgeEntityDetail,
  KnowledgeObservation,
  KnowledgeScreenshot,
  KnowledgeSummary,
  ProductOSStatus,
  Project,
  ProjectDetail,
  QueryResponse,
  Screen,
  TestCase,
  TestPlan,
  WorkItem,
} from './types'

async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  // Allow callers to override the default fetch timeout — needed for long-running
  // endpoints like POST /plans/suite which can take 60-90 seconds. Browsers
  // otherwise silently kill the request well before the server finishes.
  const timeoutMs = init?.timeoutMs ?? 30_000
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const res = await fetch(path, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers || {}),
      },
      cache: 'no-store',
      signal: controller.signal,
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`API ${res.status}: ${text}`)
    }
    if (res.status === 204) return undefined as T
    return res.json() as Promise<T>
  } catch (err: any) {
    if (err?.name === 'AbortError') {
      throw new Error(`Request timed out after ${timeoutMs / 1000}s: ${path}`)
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

export const api = {
  // Projects
  listProjects: () => request<Project[]>('/api/projects'),
  getProject: (id: number) => request<ProjectDetail>(`/api/projects/${id}`),
  createProject: (data: { name: string; app_package?: string; description?: string; enable_intelligence?: boolean; industry?: string; competitors_hint?: string }) =>
    request<Project>('/api/projects', { method: 'POST', body: JSON.stringify(data) }),
  updateProject: (id: number, data: Partial<Project>) =>
    request<Project>(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteProject: (id: number) =>
    request<void>(`/api/projects/${id}`, { method: 'DELETE' }),

  // Screens
  listScreens: (projectId: number) =>
    request<Screen[]>(`/api/projects/${projectId}/screens`),

  uploadScreensBulk: async (projectId: number, files: File[]): Promise<Screen[]> => {
    const formData = new FormData()
    files.forEach((f) => formData.append('files', f))
    const res = await fetch(`/api/projects/${projectId}/screens/bulk`, {
      method: 'POST',
      body: formData,
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`Upload failed (${res.status}): ${text}`)
    }
    return res.json()
  },

  updateScreen: (id: number, data: { name?: string; display_name?: string; purpose?: string }) =>
    request<Screen>(`/api/screens/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),

  deleteScreen: (id: number) =>
    request<void>(`/api/screens/${id}`, { method: 'DELETE' }),

  screenImageUrl: (id: number) => `/api/screens/${id}/image`,

  // Flow inference
  inferFlow: (projectId: number) =>
    request<FlowInferenceResult>(`/api/projects/${projectId}/infer-flow`, { method: 'POST' }),

  // Edges
  listEdges: (projectId: number) =>
    request<Edge[]>(`/api/projects/${projectId}/edges`),

  createEdge: (projectId: number, data: { from_screen_id: number; to_screen_id: number; trigger: string }) =>
    request<Edge>(`/api/projects/${projectId}/edges`, { method: 'POST', body: JSON.stringify(data) }),

  deleteEdge: (id: number) =>
    request<void>(`/api/edges/${id}`, { method: 'DELETE' }),

  // Test plans
  listPlans: (projectId: number) =>
    request<TestPlan[]>(`/api/projects/${projectId}/plans`),

  createPlan: (
    projectId: number,
    feature_description: string,
    opts?: { plan_type?: string; figma_file_id?: string }
  ) =>
    request<TestPlan>(`/api/projects/${projectId}/plans`, {
      method: 'POST',
      body: JSON.stringify({ feature_description, ...(opts || {}) }),
      timeoutMs: 180_000, // single plan can take up to 60s (Figma + Gemini)
    }),

  createPlanSuite: (
    projectId: number,
    feature_description: string,
    figma_file_id?: string
  ) =>
    request<TestPlan[]>(`/api/projects/${projectId}/plans/suite`, {
      method: 'POST',
      body: JSON.stringify({ feature_description, figma_file_id }),
      timeoutMs: 300_000, // suite runs 4 planners sequentially + throttle, can take 60-90s
    }),

  getPlan: (planId: number) =>
    request<TestPlan>(`/api/plans/${planId}`),

  approvePlan: (planId: number) =>
    request<TestPlan>(`/api/plans/${planId}?status=approved`, { method: 'PATCH' }),

  // Test cases
  updateCase: (caseId: number, data: Partial<TestCase>) =>
    request<TestCase>(`/api/cases/${caseId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  deleteCase: (caseId: number) =>
    request<void>(`/api/cases/${caseId}`, { method: 'DELETE' }),

  // UAT + Figma endpoints removed in v0.10.0 — moved to Loupe
  // (github.com/yash7agarwal/loupe).

  // ---------- Product OS / Knowledge ----------

  // Knowledge entities
  listEntities: (projectId: number, entityType?: string) =>
    request<KnowledgeEntity[]>(
      `/api/knowledge/entities?project_id=${projectId}${entityType ? `&entity_type=${entityType}` : ''}`
    ),
  getEntity: (id: number) =>
    request<KnowledgeEntityDetail>(`/api/knowledge/entities/${id}`),
  listEntityObservations: (id: number) =>
    request<KnowledgeObservation[]>(`/api/knowledge/entities/${id}/observations`),
  listEntityScreenshots: (id: number) =>
    request<KnowledgeScreenshot[]>(`/api/knowledge/entities/${id}/screenshots`),

  // Shortcuts
  listCompetitors: (projectId: number) =>
    request<KnowledgeEntity[]>(`/api/knowledge/competitors?project_id=${projectId}`),
  listFlows: (projectId: number) =>
    request<KnowledgeEntity[]>(`/api/knowledge/flows?project_id=${projectId}`),

  // Artifacts
  listArtifacts: (projectId: number, artifactType?: string) =>
    request<KnowledgeArtifact[]>(
      `/api/knowledge/artifacts?project_id=${projectId}${artifactType ? `&artifact_type=${artifactType}` : ''}`
    ),
  getArtifact: (id: number) =>
    request<KnowledgeArtifact>(`/api/knowledge/artifacts/${id}`),

  // Knowledge summary
  knowledgeSummary: (projectId: number) =>
    request<KnowledgeSummary>(`/api/knowledge/summary?project_id=${projectId}`),

  // Timeline
  timeline: (projectId: number, limit?: number) =>
    request<any[]>(`/api/knowledge/timeline?project_id=${projectId}${limit ? `&limit=${limit}` : ''}`),

  // Trends view
  trendsView: (projectId: number) =>
    request<any>(`/api/knowledge/trends-view?project_id=${projectId}`),

  // User feedback signal on an entity — drives the compounding loop.
  // `signal` ∈ {"kept", "dismissed", "starred", "clear"}.
  setEntitySignal: (id: number, signal: string, reason?: string) =>
    request<KnowledgeEntity>(`/api/knowledge/entities/${id}/signal`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ signal, reason }),
    }),

  // Soft-purge a mis-tagged trend: tombstones + enqueues re-research.
  purgeEntity: (id: number, reason?: string) =>
    request<any>(`/api/knowledge/entities/${id}/purge`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ signal: 'dismissed', reason: reason || '[purged via UI]' }),
    }),

  // Impact graph
  impactGraph: (projectId: number) =>
    request<any>(`/api/knowledge/impact-graph?project_id=${projectId}`),

  // Lens matrix & detail
  lensMatrix: (projectId: number) =>
    request<any>(`/api/knowledge/lens-matrix?project_id=${projectId}`),
  lensDetail: (projectId: number, lens: string) =>
    request<any>(`/api/knowledge/lens/${lens}?project_id=${projectId}`),

  // Work items & sessions
  listWorkItems: (projectId: number, agentType?: string, status?: string) =>
    request<WorkItem[]>(
      `/api/knowledge/work-items?project_id=${projectId}${agentType ? `&agent_type=${agentType}` : ''}${status ? `&status=${status}` : ''}`
    ),
  listSessions: (projectId: number, agentType?: string) =>
    request<AgentSession[]>(
      `/api/knowledge/sessions?project_id=${projectId}${agentType ? `&agent_type=${agentType}` : ''}`
    ),

  // Product OS orchestrator
  productOSStatus: (projectId: number) =>
    request<ProductOSStatus>(`/api/product-os/status?project_id=${projectId}`),
  startProductOS: (projectId: number) =>
    request<any>(`/api/product-os/start?project_id=${projectId}`, { method: 'POST' }),
  stopProductOS: (projectId: number) =>
    request<any>(`/api/product-os/stop?project_id=${projectId}`, { method: 'POST' }),
  runAgent: (projectId: number, agentType: string) =>
    request<any>(`/api/product-os/run/${agentType}?project_id=${projectId}`, { method: 'POST' }),
  runAllAgents: (projectId: number) =>
    request<any>(`/api/product-os/run-all?project_id=${projectId}`, { method: 'POST' }),
  queryKnowledge: (projectId: number, question: string) =>
    request<QueryResponse>(`/api/product-os/query`, {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId, question }),
      timeoutMs: 120_000,
    }),
  generateDigest: (projectId: number) =>
    request<{ digest: string }>(`/api/product-os/digest?project_id=${projectId}`, {
      method: 'POST',
      timeoutMs: 60_000,
    }),

  // ---- Executive reports (v0.17.0) ----
  generateReport: (projectId: number, formats: string[] = ['pdf', 'xlsx'], includeLoupe = true) =>
    request<{ job_id: string; status: string; project_id: number; formats: string[] }>(
      `/api/reports/generate?project_id=${projectId}&formats=${formats.join(',')}&include_loupe=${includeLoupe}`,
      { method: 'POST', timeoutMs: 30_000 },
    ),
  reportJobStatus: (jobId: string) =>
    request<{
      job_id: string; project_id: number; status: 'queued' | 'running' | 'done' | 'failed';
      progress: string; artifact_id: number | null; error: string | null; started_at: string;
    }>(`/api/reports/jobs/${jobId}`),
  recentReports: (projectId: number) =>
    request<Array<{
      artifact_id: number; title: string; generated_at: string;
      content_hash: string | null; stats: Record<string, number>;
      rec_count: number; loupe_runs_included: number;
    }>>(`/api/reports/recent?project_id=${projectId}`),
  reportDownloadUrl: (artifactId: number, format: 'pdf' | 'xlsx') =>
    `/api/reports/${artifactId}/download?format=${format}`,
}
