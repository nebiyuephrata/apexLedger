import {
  ActivityLog,
  ActorProfile,
  AgentPerformance,
  AgentSessionStatus,
  ApplicationSummary,
  AuditEvent,
  CommandDefinition,
  ComplianceSnapshot,
  ProjectionHealth,
} from '../types/ledger';

const wait = async (ms = 180) => new Promise((resolve) => setTimeout(resolve, ms));

const applications: ApplicationSummary[] = [
  {
    application_id: 'loan-ACME-0091',
    state: 'PENDING_DECISION',
    applicant_id: 'COMP-001',
    requested_amount_usd: 2400000,
    approved_amount_usd: null,
    risk_tier: 'MEDIUM',
    fraud_score: 0.07,
    compliance_status: 'CLEAR',
    decision_recommendation: 'APPROVE',
    agent_sessions_completed: ['sess-credit-11', 'sess-fraud-11', 'sess-comp-11'],
    human_reviewer_id: null,
    final_decision_at: null,
    last_event_type: 'DecisionRequested',
    last_event_at: '2026-03-25T09:12:00',
  },
  {
    application_id: 'loan-NEON-1422',
    state: 'FINAL_APPROVED',
    applicant_id: 'COMP-014',
    requested_amount_usd: 1100000,
    approved_amount_usd: 950000,
    risk_tier: 'LOW',
    fraud_score: 0.02,
    compliance_status: 'CLEAR',
    decision_recommendation: 'APPROVE',
    agent_sessions_completed: ['sess-credit-14', 'sess-fraud-14', 'sess-comp-14', 'sess-orch-14'],
    human_reviewer_id: 'human-1',
    final_decision_at: '2026-03-25T08:11:00',
    last_event_type: 'ApplicationApproved',
    last_event_at: '2026-03-25T08:11:00',
  },
  {
    application_id: 'loan-MT-3301',
    state: 'FINAL_DECLINED',
    applicant_id: 'COMP-777',
    requested_amount_usd: 300000,
    approved_amount_usd: null,
    risk_tier: 'MEDIUM',
    fraud_score: 0.04,
    compliance_status: 'BLOCKED',
    decision_recommendation: 'DECLINE',
    agent_sessions_completed: ['sess-credit-77', 'sess-fraud-77', 'sess-comp-77'],
    human_reviewer_id: null,
    final_decision_at: '2026-03-25T07:41:00',
    last_event_type: 'ApplicationDeclined',
    last_event_at: '2026-03-25T07:41:00',
  },
];

const complianceSnapshots: Record<string, ComplianceSnapshot> = {
  'loan-ACME-0091': {
    event_type: 'ComplianceCheckCompleted',
    overall_verdict: 'CLEAR',
    has_hard_block: false,
    rules_passed: ['REG-001', 'REG-002'],
    rules_failed: [],
    rules_noted: ['REG-005'],
  },
  'loan-MT-3301': {
    event_type: 'ComplianceRuleFailed',
    overall_verdict: 'BLOCKED',
    has_hard_block: true,
    rules_passed: ['REG-001'],
    rules_failed: ['REG-003'],
    rules_noted: [],
    rule_id: 'REG-003',
    rule_name: 'Montana Jurisdiction',
  },
};

const auditTrail: Record<string, AuditEvent[]> = {
  'loan-ACME-0091': [
    { time: '09:12', stream_id: 'loan-loan-ACME-0091', event_type: 'DecisionRequested', detail: 'Decision requested after compliance clear', global_position: 14422 },
    { time: '09:10', stream_id: 'compliance-loan-ACME-0091', event_type: 'ComplianceCheckCompleted', detail: 'Overall verdict CLEAR', global_position: 14418 },
    { time: '09:06', stream_id: 'fraud-loan-ACME-0091', event_type: 'FraudScreeningCompleted', detail: 'Fraud score 0.07', global_position: 14403 },
    { time: '09:02', stream_id: 'credit-loan-ACME-0091', event_type: 'CreditAnalysisCompleted', detail: 'Risk tier MEDIUM', global_position: 14397 },
  ],
};

const performance: AgentPerformance[] = [
  {
    agent_type: 'credit_analysis',
    model_version: 'm1',
    analyses_completed: 1284,
    avg_confidence: 0.71,
    avg_duration_ms: 92000,
    human_overrides: 54,
    override_rate: 0.042,
    updated_at: '2026-03-25T09:15:00',
  },
  {
    agent_type: 'decision_orchestrator',
    model_version: 'm1',
    analyses_completed: 911,
    avg_confidence: 0.67,
    avg_duration_ms: 18000,
    human_overrides: 39,
    override_rate: 0.043,
    updated_at: '2026-03-25T09:15:00',
  },
];

const sessions: AgentSessionStatus[] = [
  {
    session_id: 'sess-doc-42',
    agent_type: 'document_processing',
    application_id: 'loan-ACME-0091',
    last_node: 'extract_balance_sheet',
    status: 'running',
    context_source: 'fresh',
  },
  {
    session_id: 'sess-comp-77',
    agent_type: 'compliance',
    application_id: 'loan-MT-3301',
    last_node: 'evaluate_reg_003',
    status: 'needs_reconciliation',
    context_source: 'replay',
  },
  {
    session_id: 'sess-orch-14',
    agent_type: 'decision_orchestrator',
    application_id: 'loan-NEON-1422',
    last_node: 'write_output',
    status: 'completed',
    context_source: 'fresh',
  },
];

const health: ProjectionHealth = {
  lags: {
    application_summary: {
      lag_ms: 210,
      last_processed_position: 14420,
      latest_position: 14425,
      position_delta: 5,
    },
    agent_performance: {
      lag_ms: 430,
      last_processed_position: 14418,
      latest_position: 14425,
      position_delta: 7,
    },
    compliance_audit: {
      lag_ms: 620,
      last_processed_position: 14416,
      latest_position: 14425,
      position_delta: 9,
    },
  },
};

const commands: CommandDefinition[] = [
  {
    name: 'submit_application',
    description: 'Creates a new loan application and document upload request.',
    precondition: 'Unique application_id and applicant fields are required.',
  },
  {
    name: 'start_agent_session',
    description: 'Gas Town anchor for agent memory and resumability.',
    precondition: 'Must be first event in the agent session stream.',
  },
  {
    name: 'record_credit_analysis',
    description: 'Appends CreditAnalysisCompleted and triggers fraud screening.',
    precondition: 'Agent session must be active and context-loaded.',
  },
  {
    name: 'record_fraud_screening',
    description: 'Appends FraudScreeningCompleted and triggers compliance.',
    precondition: 'fraud_score must be between 0.0 and 1.0.',
  },
  {
    name: 'record_compliance_check',
    description: 'Writes deterministic rule results to the compliance stream.',
    precondition: 'rule_id must belong to the active regulation set.',
  },
  {
    name: 'generate_decision',
    description: 'Creates DecisionGenerated while enforcing the confidence floor.',
    precondition: 'Application must be ready for decision and causal sessions must match.',
  },
  {
    name: 'record_human_review',
    description: 'Final binding approval or decline by a human reviewer.',
    precondition: 'reviewer_id is required; override_reason is required for overrides.',
  },
  {
    name: 'run_integrity_check',
    description: 'Runs the SHA-256 audit chain verification.',
    precondition: 'Rate-limited to one run per entity per minute.',
  },
];

const actorProfiles: ActorProfile[] = [
  {
    role: 'loan_officer',
    title: 'Loan Officer',
    focus: 'Operational dashboard and final human decisioning.',
    resources: ['ledger://applications/{id}', 'ledger://ledger/health'],
    tools: ['record_human_review'],
  },
  {
    role: 'compliance_officer',
    title: 'Compliance Officer',
    focus: 'Temporal compliance audits and hard-block review.',
    resources: ['ledger://applications/{id}/compliance', 'ledger://applications/{id}/audit-trail'],
    tools: ['record_compliance_check', 'run_integrity_check'],
  },
  {
    role: 'security_officer',
    title: 'Security Officer',
    focus: 'Audit chain, outbox reliability, and operational logs.',
    resources: ['ledger://applications/{id}/audit-trail', 'ledger://ledger/health'],
    tools: ['run_integrity_check'],
  },
  {
    role: 'admin',
    title: 'System Admin',
    focus: 'Projection lag, model performance, and rebuild operations.',
    resources: ['ledger://agents/{id}/performance', 'ledger://ledger/health'],
    tools: ['start_agent_session', 'submit_application'],
  },
  {
    role: 'auditor',
    title: 'Auditor',
    focus: 'Independent replay, temporal review, and export package generation.',
    resources: ['ledger://applications/{id}/compliance', 'ledger://applications/{id}/audit-trail'],
    tools: ['run_integrity_check'],
  },
  {
    role: 'applicant',
    title: 'Applicant',
    focus: 'Document submission and application status tracking.',
    resources: ['ledger://applications/{id}'],
    tools: ['submit_application'],
  },
  {
    role: 'user_proxy',
    title: 'UserProxy Agent',
    focus: 'Human-in-the-loop proxy for task delegation and monitoring.',
    resources: ['ledger://applications/{id}', 'ledger://agents/{id}/sessions/{session_id}'],
    tools: ['start_agent_session'],
  },
];

const logs: ActivityLog[] = [
  {
    id: 'log-001',
    level: 'INFO',
    component: 'projection-daemon',
    message: 'Processed global positions 14421-14520 and updated checkpoints atomically.',
    timestamp: '2026-03-25 09:16:12',
  },
  {
    id: 'log-002',
    level: 'WARN',
    component: 'compliance-agent',
    message: 'REG-003 hard block triggered; halted downstream evaluation and declined application.',
    timestamp: '2026-03-25 09:14:19',
  },
  {
    id: 'log-003',
    level: 'ERROR',
    component: 'projection-daemon',
    message: 'Malformed CreditAnalysisCompleted payload retried 3 times, then skipped as poison pill.',
    timestamp: '2026-03-25 09:12:27',
  },
];

export const ledgerMock = {
  async getSession() {
    await wait();
    return {
      user_id: 'u-loan-01',
      role: 'loan_officer' as const,
      org_id: 'org_demo',
      is_internal: false,
      auth_source: 'mock',
      permissions: [],
      allowed_tools: ['submit_application', 'record_human_review'],
      allowed_resources: ['ledger://applications/{id}', 'ledger://ledger/health'],
      allowed_views: ['dashboard'],
      capabilities: ['dashboard', 'submit_application', 'record_human_review'],
    };
  },
  async getApplicationSummaries() {
    await wait();
    return applications;
  },
  async getApplicationSummary(applicationId: string) {
    await wait();
    return applications.find((item) => item.application_id === applicationId) ?? null;
  },
  async getComplianceView(applicationId: string, _asOf?: string) {
    await wait();
    return complianceSnapshots[applicationId] ?? null;
  },
  async getAuditTrail(applicationId: string) {
    await wait();
    return auditTrail[applicationId] ?? [];
  },
  async getAgentPerformance(agentType?: string) {
    await wait();
    return agentType ? performance.filter((item) => item.agent_type === agentType) : performance;
  },
  async getAgentSessions() {
    await wait();
    return sessions;
  },
  async getHealth() {
    await wait(120);
    return health;
  },
  async getCommandCatalog() {
    await wait();
    return commands;
  },
  async getActorProfiles() {
    await wait();
    return actorProfiles;
  },
  async getLogs() {
    await wait();
    return logs;
  },
  async invokeTool(toolName: string) {
    await wait(120);
    return {
      ok: true,
      result: { tool: toolName },
      error: null,
    };
  },
};
