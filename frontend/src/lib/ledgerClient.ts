import {
  ActivityLog,
  ActorProfile,
  AgentPerformance,
  AgentSessionStatus,
  ApiEnvelope,
  ApiErrorPayload,
  ApplicationSummary,
  AuditEvent,
  CommandDefinition,
  ComplianceSnapshot,
  PaginatedResult,
  ProjectionHealth,
  RuntimeSnapshot,
  SessionContext,
  ToolExecutionResult,
} from '../types/ledger';
import { ledgerMock } from './ledgerMock';
import { getAuthHeaders } from '../platform/http';

type InvokePayload = Record<string, unknown>;

export class LedgerApiError extends Error {
  status: number;
  code: string;
  context: Record<string, unknown>;
  suggestedAction?: string | null;
  requestId?: string | null;
  idempotentReplay: boolean;

  constructor(status: number, error: ApiErrorPayload, idempotentReplay = false) {
    super(error.message);
    this.name = 'LedgerApiError';
    this.status = status;
    this.code = error.error_type;
    this.context = error.context ?? {};
    this.suggestedAction = error.suggested_action;
    this.requestId = error.request_id;
    this.idempotentReplay = idempotentReplay;
  }
}

type ToolInvocationResponse = {
  result: ToolExecutionResult | null;
  meta: ApiEnvelope<ToolExecutionResult>['meta'];
};

type LedgerClient = {
  getSession(): Promise<SessionContext>;
  getApplicationSummaries(params?: { page?: number; pageSize?: number; search?: string; state?: string }): Promise<PaginatedResult<ApplicationSummary>>;
  getApplicationSummary(applicationId: string): Promise<ApplicationSummary | null>;
  getComplianceView(applicationId: string, asOf?: string): Promise<ComplianceSnapshot | null>;
  getAuditTrail(applicationId: string, params?: { page?: number; pageSize?: number; eventType?: string }): Promise<PaginatedResult<AuditEvent>>;
  getAgentPerformance(agentType?: string): Promise<AgentPerformance[]>;
  getAgentSessions(params?: { page?: number; pageSize?: number; agentType?: string }): Promise<PaginatedResult<AgentSessionStatus>>;
  getHealth(): Promise<ProjectionHealth>;
  getCommandCatalog(): Promise<CommandDefinition[]>;
  getActorProfiles(): Promise<ActorProfile[]>;
  getLogs(params?: { page?: number; pageSize?: number; level?: string; search?: string }): Promise<PaginatedResult<ActivityLog>>;
  getRuntime(): Promise<RuntimeSnapshot>;
  invokeTool(toolName: string, payload?: InvokePayload, idempotencyKey?: string): Promise<ToolInvocationResponse>;
};

const apiBaseUrl = import.meta.env.VITE_LEDGER_API_BASE_URL?.replace(/\/$/, '') ?? '';
const useMock =
  import.meta.env.VITE_LEDGER_USE_MOCK === 'true' ||
  (import.meta.env.DEV && apiBaseUrl.length === 0);

const buildQuery = (params?: Record<string, string | number | undefined>) => {
  const entries = Object.entries(params ?? {}).filter(([, value]) => value !== undefined && value !== '');
  if (!entries.length) return '';
  return `?${new URLSearchParams(entries.map(([key, value]) => [key, String(value)])).toString()}`;
};

const fetchEnvelope = async <T>(path: string, init?: RequestInit): Promise<{ data: T; meta: ApiEnvelope<T>['meta'] }> => {
  const authHeaders = await getAuthHeaders();
  const response = await fetch(`${apiBaseUrl}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders,
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  const body = (await response.json()) as ApiEnvelope<T>;
  const idempotentReplay = response.headers.get('X-Idempotent-Replay') === 'true' || Boolean(body.meta?.idempotent_replay);

  if (!response.ok || !body.ok) {
    throw new LedgerApiError(
      response.status,
      body.error ?? {
        error_type: 'ApiError',
        message: `Ledger API error ${response.status}`,
        context: {},
      },
      idempotentReplay,
    );
  }

  return {
    data: body.result as T,
    meta: body.meta,
  };
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
      async () => (await fetchEnvelope<SessionContext>('/api/session')).data,
      async () => ({
        user_id: import.meta.env.VITE_LEDGER_DEV_USER_ID ?? 'u-loan-01',
        role: (import.meta.env.VITE_LEDGER_DEV_ROLE ?? 'loan_officer') as SessionContext['role'],
        org_id: import.meta.env.VITE_LEDGER_DEV_ORG_ID ?? 'org_demo',
        is_internal: import.meta.env.VITE_LEDGER_DEV_INTERNAL === 'true',
        identity_type: 'human',
        auth_source: 'mock',
        permissions: [],
        allowed_tools: ['submit_application'],
        allowed_resources: ['ledger://applications/{id}', 'ledger://ledger/health'],
        allowed_views: ['dashboard'],
        capabilities: ['submit_application', 'dashboard'],
        session_mode: 'interactive',
      }),
    );
  },
  getApplicationSummaries(params) {
    return withDevFallback(
      async () =>
        (
          await fetchEnvelope<PaginatedResult<ApplicationSummary>>(
            `/api/applications${buildQuery({
              page: params?.page,
              page_size: params?.pageSize,
              search: params?.search,
              state: params?.state,
            })}`,
          )
        ).data,
      async () => ({
        items: await ledgerMock.getApplicationSummaries(),
        page: params?.page ?? 1,
        page_size: params?.pageSize ?? 25,
        total: (await ledgerMock.getApplicationSummaries()).length,
        has_more: false,
      }),
    );
  },
  getApplicationSummary(applicationId) {
    return withDevFallback(
      async () => (await fetchEnvelope<ApplicationSummary | null>(`/api/applications/${encodeURIComponent(applicationId)}`)).data,
      () => ledgerMock.getApplicationSummary(applicationId),
    );
  },
  getComplianceView(applicationId, asOf) {
    return withDevFallback(
      async () =>
        (
          await fetchEnvelope<ComplianceSnapshot | null>(
            `/api/applications/${encodeURIComponent(applicationId)}/compliance${buildQuery({ as_of: asOf })}`,
          )
        ).data,
      () => ledgerMock.getComplianceView(applicationId, asOf),
    );
  },
  getAuditTrail(applicationId, params) {
    return withDevFallback(
      async () =>
        (
          await fetchEnvelope<PaginatedResult<AuditEvent>>(
            `/api/applications/${encodeURIComponent(applicationId)}/audit-trail${buildQuery({
              page: params?.page,
              page_size: params?.pageSize,
              event_type: params?.eventType,
            })}`,
          )
        ).data,
      async () => ({
        items: await ledgerMock.getAuditTrail(applicationId),
        page: params?.page ?? 1,
        page_size: params?.pageSize ?? 50,
        total: (await ledgerMock.getAuditTrail(applicationId)).length,
        has_more: false,
      }),
    );
  },
  getAgentPerformance(agentType) {
    return withDevFallback(
      async () => (await fetchEnvelope<AgentPerformance[]>(`/api/agents/performance${buildQuery({ agent_type: agentType })}`)).data,
      () => ledgerMock.getAgentPerformance(agentType),
    );
  },
  getAgentSessions(params) {
    return withDevFallback(
      async () =>
        (
          await fetchEnvelope<PaginatedResult<AgentSessionStatus>>(
            `/api/agents/sessions${buildQuery({
              page: params?.page,
              page_size: params?.pageSize,
              agent_type: params?.agentType,
            })}`,
          )
        ).data,
      async () => ({
        items: await ledgerMock.getAgentSessions(),
        page: params?.page ?? 1,
        page_size: params?.pageSize ?? 25,
        total: (await ledgerMock.getAgentSessions()).length,
        has_more: false,
      }),
    );
  },
  getHealth() {
    return withDevFallback(
      async () => (await fetchEnvelope<ProjectionHealth>('/api/ledger/health')).data,
      () => ledgerMock.getHealth(),
    );
  },
  getCommandCatalog() {
    return withDevFallback(
      async () => (await fetchEnvelope<CommandDefinition[]>('/api/meta/commands')).data,
      () => ledgerMock.getCommandCatalog(),
    );
  },
  getActorProfiles() {
    return withDevFallback(
      async () => (await fetchEnvelope<ActorProfile[]>('/api/meta/actors')).data,
      () => ledgerMock.getActorProfiles(),
    );
  },
  getLogs(params) {
    return withDevFallback(
      async () =>
        (
          await fetchEnvelope<PaginatedResult<ActivityLog>>(
            `/api/ops/logs${buildQuery({
              page: params?.page,
              page_size: params?.pageSize,
              level: params?.level,
              search: params?.search,
            })}`,
          )
        ).data,
      async () => ({
        items: await ledgerMock.getLogs(),
        page: params?.page ?? 1,
        page_size: params?.pageSize ?? 50,
        total: (await ledgerMock.getLogs()).length,
        has_more: false,
      }),
    );
  },
  getRuntime() {
    return withDevFallback(
      async () => (await fetchEnvelope<RuntimeSnapshot>('/api/ops/runtime')).data,
      async () => ({
        cache_hits: 0,
        cache_misses: 0,
        cache_invalidations: 0,
        db_queries: 0,
        avg_db_latency_ms: 0,
        routes: {},
        recent_actions: [],
      }),
    );
  },
  invokeTool(toolName, payload = {}, idempotencyKey = crypto.randomUUID()) {
    return withDevFallback(
      async () => {
        const envelope = await fetchEnvelope<ToolExecutionResult>(`/api/tools/${encodeURIComponent(toolName)}`, {
          method: 'POST',
          headers: {
            'Idempotency-Key': idempotencyKey,
          },
          body: JSON.stringify(payload),
        });
        return { result: envelope.data, meta: envelope.meta };
      },
      async () => ({ result: await ledgerMock.invokeTool(toolName), meta: { request_id: 'mock', idempotency_key: idempotencyKey, idempotent_replay: false } }),
    );
  },
};

export const ledgerClient: LedgerClient = apiClient;

export const ledgerApiConfig = {
  apiBaseUrl,
  useMock,
};
