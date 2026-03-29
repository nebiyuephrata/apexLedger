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
  SessionContext,
} from '../types/ledger';
import { ledgerMock } from './ledgerMock';
import { getAuthHeaders } from '../platform/http';

type InvokePayload = Record<string, unknown>;

type LedgerClient = {
  getSession(): Promise<SessionContext>;
  getApplicationSummaries(): Promise<ApplicationSummary[]>;
  getApplicationSummary(applicationId: string): Promise<ApplicationSummary | null>;
  getComplianceView(applicationId: string, asOf?: string): Promise<ComplianceSnapshot | null>;
  getAuditTrail(applicationId: string): Promise<AuditEvent[]>;
  getAgentPerformance(agentType?: string): Promise<AgentPerformance[]>;
  getAgentSessions(): Promise<AgentSessionStatus[]>;
  getHealth(): Promise<ProjectionHealth>;
  getCommandCatalog(): Promise<CommandDefinition[]>;
  getActorProfiles(): Promise<ActorProfile[]>;
  getLogs(limit?: number): Promise<ActivityLog[]>;
  invokeTool(toolName: string, payload?: InvokePayload, idempotencyKey?: string): Promise<unknown>;
};

const apiBaseUrl = import.meta.env.VITE_LEDGER_API_BASE_URL?.replace(/\/$/, '') ?? '';
const useMock =
  import.meta.env.VITE_LEDGER_USE_MOCK === 'true' ||
  (import.meta.env.DEV && apiBaseUrl.length === 0);

const fetchJson = async <T>(path: string, init?: RequestInit): Promise<T> => {
  const authHeaders = await getAuthHeaders();
  const response = await fetch(`${apiBaseUrl}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders,
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(`Ledger API error ${response.status}: ${message || response.statusText}`);
  }

  return response.json() as Promise<T>;
};

const withDevFallback = async <T>(request: () => Promise<T>, fallback: () => Promise<T>): Promise<T> => {
  try {
    return await request();
  } catch (error) {
    if (import.meta.env.DEV) {
      console.warn('Ledger API unavailable, falling back to mock data.', error);
      return fallback();
    }
    throw error;
  }
};

const apiClient: LedgerClient = {
  getSession() {
    return withDevFallback(
      () => fetchJson<SessionContext>('/api/session'),
      async () => ({
        user_id: import.meta.env.VITE_LEDGER_DEV_USER_ID ?? 'u-loan-01',
        role: (import.meta.env.VITE_LEDGER_DEV_ROLE ?? 'loan_officer') as SessionContext['role'],
        org_id: import.meta.env.VITE_LEDGER_DEV_ORG_ID ?? 'org_demo',
        is_internal: import.meta.env.VITE_LEDGER_DEV_INTERNAL === 'true',
        auth_source: 'mock',
        permissions: [],
        allowed_tools: ['submit_application'],
        allowed_resources: ['ledger://applications/{id}', 'ledger://ledger/health'],
        allowed_views: ['dashboard'],
        capabilities: ['submit_application', 'dashboard'],
      }),
    );
  },
  getApplicationSummaries() {
    return withDevFallback(
      () => fetchJson<ApplicationSummary[]>('/api/applications'),
      () => ledgerMock.getApplicationSummaries(),
    );
  },
  getApplicationSummary(applicationId) {
    return withDevFallback(
      () => fetchJson<ApplicationSummary | null>(`/api/applications/${encodeURIComponent(applicationId)}`),
      () => ledgerMock.getApplicationSummary(applicationId),
    );
  },
  getComplianceView(applicationId, asOf) {
    const query = asOf ? `?as_of=${encodeURIComponent(asOf)}` : '';
    return withDevFallback(
      () => fetchJson<ComplianceSnapshot | null>(`/api/applications/${encodeURIComponent(applicationId)}/compliance${query}`),
      () => ledgerMock.getComplianceView(applicationId, asOf),
    );
  },
  getAuditTrail(applicationId) {
    return withDevFallback(
      () => fetchJson<AuditEvent[]>(`/api/applications/${encodeURIComponent(applicationId)}/audit-trail`),
      () => ledgerMock.getAuditTrail(applicationId),
    );
  },
  getAgentPerformance(agentType) {
    const query = agentType ? `?agent_type=${encodeURIComponent(agentType)}` : '';
    return withDevFallback(
      () => fetchJson<AgentPerformance[]>(`/api/agents/performance${query}`),
      () => ledgerMock.getAgentPerformance(agentType),
    );
  },
  getAgentSessions() {
    return withDevFallback(
      () => fetchJson<AgentSessionStatus[]>('/api/agents/sessions'),
      () => ledgerMock.getAgentSessions(),
    );
  },
  getHealth() {
    return withDevFallback(
      () => fetchJson<ProjectionHealth>('/api/ledger/health'),
      () => ledgerMock.getHealth(),
    );
  },
  getCommandCatalog() {
    return withDevFallback(
      () => fetchJson<CommandDefinition[]>('/api/meta/commands'),
      () => ledgerMock.getCommandCatalog(),
    );
  },
  getActorProfiles() {
    return withDevFallback(
      () => fetchJson<ActorProfile[]>('/api/meta/actors'),
      () => ledgerMock.getActorProfiles(),
    );
  },
  getLogs(limit = 50) {
    return withDevFallback(
      () => fetchJson<ActivityLog[]>(`/api/ops/logs?limit=${encodeURIComponent(String(limit))}`),
      () => ledgerMock.getLogs(),
    );
  },
  invokeTool(toolName, payload = {}, idempotencyKey = crypto.randomUUID()) {
    return withDevFallback(
      () =>
        fetchJson(`/api/tools/${encodeURIComponent(toolName)}`, {
          method: 'POST',
          headers: {
            'Idempotency-Key': idempotencyKey,
          },
          body: JSON.stringify(payload),
        }),
      () => ledgerMock.invokeTool(toolName),
    );
  },
};

export const ledgerClient: LedgerClient = useMock ? ledgerMock as LedgerClient : apiClient;

export const ledgerApiConfig = {
  apiBaseUrl,
  useMock,
};
