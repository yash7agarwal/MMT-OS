// Shared types between frontend and FastAPI backend.
// Mirror of webapp/api/schemas.py.

export interface Project {
  id: number
  name: string
  app_package: string | null
  description: string | null
  created_at: string
}

export interface ProjectStats {
  screen_count: number
  edge_count: number
  plan_count: number
}

export interface ProjectDetail extends Project {
  stats: ProjectStats
}

export interface ScreenElement {
  label: string
  type: string
  x_pct: number
  y_pct: number
  leads_to_hint?: string | null
}

export interface Screen {
  id: number
  project_id: number
  name: string
  display_name: string | null
  purpose: string | null
  screenshot_path: string
  elements: ScreenElement[] | null
  discovered_at: string
  last_updated: string
}

export interface Edge {
  id: number
  project_id: number
  from_screen_id: number
  to_screen_id: number
  trigger: string
}

export interface InferredEdge {
  from_screen_id: number
  to_screen_id: number
  trigger: string
  confidence: number
  reasoning: string
}

export interface FlowInferenceResult {
  proposed_edges: InferredEdge[]
  home_screen_id: number | null
  branches: { name: string; screen_ids: number[]; reasoning: string }[]
}

export interface NavigationStep {
  from_screen: string
  to_screen: string
  trigger: string
}

export interface TestCase {
  id: number
  plan_id: number
  title: string
  target_screen_id: number | null
  navigation_path: NavigationStep[] | null
  acceptance_criteria: string
  branch_label: string | null
  status: 'proposed' | 'approved' | 'removed'
}

export interface TestPlan {
  id: number
  project_id: number
  feature_description: string
  voice_transcript: string | null
  status: 'draft' | 'approved'
  created_at: string
  cases: TestCase[]
}
