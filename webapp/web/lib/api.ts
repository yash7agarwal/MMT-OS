// Typed fetch client for the FastAPI backend.
// All requests go through Next.js rewrite proxy → http://localhost:8000

import type {
  Edge,
  FigmaImport,
  FigmaImportSummary,
  FlowInferenceResult,
  InferredEdge,
  Project,
  ProjectDetail,
  Screen,
  TestCase,
  TestPlan,
  UatRun,
  UatRunSummary,
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
  createProject: (data: { name: string; app_package?: string; description?: string }) =>
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

  // ---------- UAT runs ----------

  listUatRuns: (projectId: number) =>
    request<UatRunSummary[]>(`/api/projects/${projectId}/uat/runs`),

  getUatRun: (runId: number) =>
    request<UatRun>(`/api/uat/runs/${runId}`),

  createUatRun: (
    projectId: number,
    data: {
      apk_path?: string | null
      figma_file_id: string
      feature_description?: string | null
      skip_install?: boolean
    }
  ) =>
    request<UatRun>(`/api/projects/${projectId}/uat/runs`, {
      method: 'POST',
      body: JSON.stringify(data),
      timeoutMs: 600_000, // runs can take 60-300s depending on frame count + device speed
    }),

  deleteUatRun: (runId: number) =>
    request<void>(`/api/uat/runs/${runId}`, { method: 'DELETE' }),

  // Image URLs (not JSON — used as <img src=...>)
  uatFigmaImageUrl: (runId: number, frameId: number) =>
    `/api/uat/runs/${runId}/frames/${frameId}/figma_image`,
  uatAppScreenshotUrl: (runId: number, frameId: number) =>
    `/api/uat/runs/${runId}/frames/${frameId}/app_screenshot`,
  uatDiffImageUrl: (runId: number, frameId: number) =>
    `/api/uat/runs/${runId}/frames/${frameId}/diff_image`,
  uatReportMdUrl: (runId: number) =>
    `/api/uat/runs/${runId}/report.md`,

  // ---------- Figma imports ----------

  listFigmaImports: (projectId: number) =>
    request<FigmaImportSummary[]>(`/api/projects/${projectId}/figma/imports`),

  getFigmaImport: (importId: number) =>
    request<FigmaImport>(`/api/figma/imports/${importId}`),

  createFigmaImport: (projectId: number, figma_file_id: string) =>
    request<FigmaImport>(`/api/projects/${projectId}/figma/imports`, {
      method: 'POST',
      body: JSON.stringify({ figma_file_id }),
      timeoutMs: 180_000, // import takes 30-90s for typical files
    }),

  deleteFigmaImport: (importId: number) =>
    request<void>(`/api/figma/imports/${importId}`, { method: 'DELETE' }),

  figmaFrameImageUrl: (importId: number, frameId: number) =>
    `/api/figma/imports/${importId}/frames/${frameId}/image`,
}
