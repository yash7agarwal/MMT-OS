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
  entity_count: number
  observation_count: number
  competitor_count: number
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

export type PlanType =
  | 'feature_flow'
  | 'design_fidelity'
  | 'functional_flow'
  | 'deeplink_utility'
  | 'edge_cases'

export interface TestPlan {
  id: number
  project_id: number
  feature_description: string
  voice_transcript: string | null
  status: 'draft' | 'approved'
  plan_type: PlanType
  created_at: string
  cases: TestCase[]
}

// ---------- UAT runs ----------

export type UatVerdict = 'MATCHES' | 'DIFFERS' | 'UNREACHABLE' | 'ERROR'
export type UatRunStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface UatFrameResult {
  id: number
  run_id: number
  figma_frame_name: string
  figma_node_id: string
  figma_image_path: string | null
  app_screenshot_path: string | null
  diff_image_path: string | null
  match_score: number | null
  verdict: UatVerdict
  issues: string[] | null
  navigation_steps: number
  elapsed_s: number | null
}

export interface UatRun {
  id: number
  project_id: number
  apk_path: string | null
  apk_version: string | null
  package_name: string | null
  figma_file_id: string | null
  feature_description: string | null
  status: UatRunStatus
  total_frames: number
  matched: number
  mismatched: number
  unreachable: number
  overall_match_score: number | null
  report_md_path: string | null
  error: string | null
  started_at: string
  completed_at: string | null
  frame_results: UatFrameResult[]
}

export interface UatRunSummary {
  id: number
  project_id: number
  apk_version: string | null
  figma_file_id: string | null
  feature_description: string | null
  status: UatRunStatus
  total_frames: number
  matched: number
  mismatched: number
  unreachable: number
  overall_match_score: number | null
  started_at: string
  completed_at: string | null
}

// ---------- Figma imports ----------

export type FigmaImportStatus = 'fetching' | 'ready' | 'failed'

export interface FigmaFrame {
  id: number
  import_id: number
  node_id: string
  name: string
  page_name: string | null
  frame_type: string
  image_path: string | null
  width: number | null
  height: number | null
  x: number | null
  y: number | null
  text_content: any[] | null
  colors: string[] | null
  fonts: { family: string; size: number; weight: number }[] | null
}

export interface FigmaImportSummary {
  id: number
  project_id: number
  figma_file_id: string
  file_name: string | null
  status: FigmaImportStatus
  total_frames: number
  error: string | null
  imported_at: string
  completed_at: string | null
}

export interface FigmaImport extends FigmaImportSummary {
  frames: FigmaFrame[]
}

// ---------- Product OS / Knowledge Graph ----------

export interface KnowledgeEntity {
  id: number
  project_id: number
  entity_type: string
  name: string
  canonical_name: string | null
  description: string | null
  metadata_json: Record<string, any> | null
  source_agent: string | null
  confidence: number
  first_seen_at: string
  last_updated_at: string
}

export interface KnowledgeEntityDetail extends KnowledgeEntity {
  observations: KnowledgeObservation[]
  relations: KnowledgeRelation[]
}

export interface KnowledgeRelation {
  id: number
  from_entity_id: number
  to_entity_id: number
  relation_type: string
  metadata_json: Record<string, any> | null
  source_agent: string | null
  created_at: string
}

export interface KnowledgeObservation {
  id: number
  entity_id: number
  observation_type: string
  content: string
  evidence_json: Record<string, any> | null
  observed_at: string
  recorded_at: string
  source_url: string | null
  source_agent: string | null
}

export interface KnowledgeArtifact {
  id: number
  project_id: number
  artifact_type: string
  title: string
  content_md: string
  entity_ids_json: number[] | null
  generated_by_agent: string | null
  generated_at: string
  is_stale: boolean
}

export interface KnowledgeScreenshot {
  id: number
  entity_id: number | null
  project_id: number
  file_path: string
  thumbnail_path: string | null
  screen_label: string | null
  app_package: string | null
  app_version: string | null
  visual_hash: string | null
  captured_at: string
  captured_by_agent: string | null
  flow_session_id: string | null
  sequence_order: number | null
}

export interface WorkItem {
  id: number
  project_id: number
  agent_type: string
  priority: number
  category: string
  description: string
  status: string
  result_summary: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface AgentSession {
  id: number
  project_id: number
  agent_type: string
  started_at: string
  completed_at: string | null
  items_completed: number
  items_failed: number
  knowledge_added: number
  session_summary: string | null
}

export interface KnowledgeSummary {
  entity_count_by_type: Record<string, number>
  total_observations: number
  total_artifacts: number
  total_screenshots: number
  stale_artifact_count: number
}

export interface ProductOSStatus {
  is_running: boolean
  project_id: number
  agents: Record<string, {
    last_session: AgentSession | null
    pending_work_items: number
    total_sessions: number
    config: Record<string, any>
  }>
  knowledge: Record<string, number>
}

export interface QueryResponse {
  answer: string
  sources: Array<{ entity_id: number; type: string; name: string }>
  screenshots: Array<{ id: number; path: string; label: string }>
  confidence: number
  data_freshness: string
  follow_up_questions: string[]
}
