import { Role } from '../auth/AuthProvider';

export type ApplicationSummary = {
  application_id: string;
  state: string | null;
  applicant_id: string | null;
  tenant_id?: string | null;
  owner_user_id?: string | null;
  requested_amount_usd: number | null;
  approved_amount_usd: number | null;
  risk_tier: string | null;
  fraud_score: number | null;
  compliance_status: string | null;
  decision_recommendation: string | null;
  agent_sessions_completed: string[];
  human_reviewer_id: string | null;
  final_decision_at: string | null;
  last_event_type: string | null;
  last_event_at: string | null;
};

export type ComplianceSnapshot = {
  event_type?: string;
  overall_verdict?: string | null;
  has_hard_block?: boolean | null;
  rules_passed?: string[] | null;
  rules_failed?: string[] | null;
  rules_noted?: string[] | null;
  rule_id?: string | null;
  rule_name?: string | null;
};

export type AuditEvent = {
  time: string;
  stream_id: string;
  event_type: string;
  detail: string;
  global_position?: number;
};

export type AgentPerformance = {
  agent_type: string;
  model_version: string;
  analyses_completed: number;
  avg_confidence: number;
  avg_duration_ms: number;
  human_overrides: number;
  override_rate: number;
  updated_at: string;
};

export type ProjectionHealth = {
  lags: Record<
    string,
    {
      lag_ms: number;
      last_processed_position: number;
      latest_position: number;
      position_delta: number;
    }
  >;
};

export type AgentSessionStatus = {
  session_id: string;
  agent_type: string;
  application_id: string;
  last_node: string;
  status: 'running' | 'waiting' | 'needs_reconciliation' | 'completed';
  context_source: string;
};

export type CommandDefinition = {
  name: string;
  description: string;
  precondition: string;
};

export type ActivityLog = {
  id: string;
  level: 'INFO' | 'WARN' | 'ERROR';
  component: string;
  message: string;
  timestamp: string;
};

export type ActorProfile = {
  role: Role;
  title: string;
  focus: string;
  resources: string[];
  tools: string[];
};

export type SessionContext = {
  user_id: string;
  role: Role;
  org_id: string | null;
  is_internal: boolean;
  auth_source: string;
  display_name?: string | null;
  permissions: string[];
  allowed_tools: string[];
  allowed_resources: string[];
  allowed_views: string[];
  capabilities: string[];
};
