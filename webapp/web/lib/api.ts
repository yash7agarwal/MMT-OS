// Typed fetch client for the FastAPI backend.
// All requests go through Next.js rewrite proxy → http://localhost:8000

import type {
  Edge,
  FlowInferenceResult,
  InferredEdge,
  Project,
  ProjectDetail,
  Screen,
  TestCase,
  TestPlan,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
    cache: 'no-store',
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`API ${res.status}: ${text}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
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

  createPlan: (projectId: number, feature_description: string) =>
    request<TestPlan>(`/api/projects/${projectId}/plans`, {
      method: 'POST',
      body: JSON.stringify({ feature_description }),
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
}
