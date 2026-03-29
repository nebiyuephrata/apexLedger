import {
  ActivityLog,
  ActorProfile,
  AgentInteraction,
  AgentPerformance,
  AgentSessionStatus,
  ApiEnvelope,
  ApiErrorPayload,
  ApplicationSummary,
  AuditEvent,
  CommandDefinition,
  ComplianceSnapshot,
  DocumentUploadResult,
  PaginatedResult,
  ProjectionHealth,
  RuntimeSnapshot,
  SessionContext,
  ToolExecutionResult,
} from '../types/ledger';
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
  getAgentInteractions(params: { applicationId: string; agentType?: string; limit?: number }): Promise<AgentInteraction[]>;
  getHealth(): Promise<ProjectionHealth>;
  getCommandCatalog(): Promise<CommandDefinition[]>;
  getActorProfiles(): Promise<ActorProfile[]>;
  getLogs(params?: { page?: number; pageSize?: number; level?: string; search?: string }): Promise<PaginatedResult<ActivityLog>>;
  getRuntime(): Promise<RuntimeSnapshot>;
  uploadDocument(payload: { applicationId: string; documentType: string; fiscalYear?: string; file: File }): Promise<DocumentUploadResult>;
  invokeTool(toolName: string, payload?: InvokePayload, idempotencyKey?: string): Promise<ToolInvocationResponse>;
};

const apiBaseUrl = import.meta.env.VITE_LEDGER_API_BASE_URL?.replace(/\/$/, '') ?? '';

const buildQuery = (params?: Record<string, string | number | undefined>) => {
  const entries = Object.entries(params ?? {}).filter(([, value]) => value !== undefined && value !== '');
  if (!entries.length) return '';
  return `?${new URLSearchParams(entries.map(([key, value]) => [key, String(value)])).toString()}`;
};

const resolveEnvelope = async <T>(response: Response): Promise<{ data: T; meta: ApiEnvelope<T>['meta'] }> => {
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
  return { data: body.result as T, meta: body.meta };
};

const fetchEnvelope = async <T>(path: string, init?: RequestInit): Promise<{ data: T; meta: ApiEnvelope<T>['meta'] }> => {
  const authHeaders = await getAuthHeaders();
  const response = await fetch(`${apiBaseUrl}${path}`, {
    headers: {
      ...authHeaders,
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  return resolveEnvelope<T>(response);
};

const apiClient: LedgerClient = {
  async getSession() {
    return (await fetchEnvelope<SessionContext>('/api/session')).data;
  },
  async getApplicationSummaries(params) {
    return (
      await fetchEnvelope<PaginatedResult<ApplicationSummary>>(
        `/api/applications${buildQuery({
          page: params?.page,
          page_size: params?.pageSize,
          search: params?.search,
          state: params?.state,
        })}`,
      )
    ).data;
  },
  async getApplicationSummary(applicationId) {
    return (await fetchEnvelope<ApplicationSummary | null>(`/api/applications/${encodeURIComponent(applicationId)}`)).data;
  },
  async getComplianceView(applicationId, asOf) {
    return (
      await fetchEnvelope<ComplianceSnapshot | null>(
        `/api/applications/${encodeURIComponent(applicationId)}/compliance${buildQuery({ as_of: asOf })}`,
      )
    ).data;
  },
  async getAuditTrail(applicationId, params) {
    return (
      await fetchEnvelope<PaginatedResult<AuditEvent>>(
        `/api/applications/${encodeURIComponent(applicationId)}/audit-trail${buildQuery({
          page: params?.page,
          page_size: params?.pageSize,
          event_type: params?.eventType,
        })}`,
      )
    ).data;
  },
  async getAgentPerformance(agentType) {
    return (await fetchEnvelope<AgentPerformance[]>(`/api/agents/performance${buildQuery({ agent_type: agentType })}`)).data;
  },
  async getAgentSessions(params) {
    return (
      await fetchEnvelope<PaginatedResult<AgentSessionStatus>>(
        `/api/agents/sessions${buildQuery({
          page: params?.page,
          page_size: params?.pageSize,
          agent_type: params?.agentType,
        })}`,
      )
    ).data;
  },
  async getAgentInteractions(params) {
    return (
      await fetchEnvelope<AgentInteraction[]>(
        `/api/agents/interactions${buildQuery({
          application_id: params.applicationId,
          agent_type: params.agentType,
          limit: params.limit,
        })}`,
      )
    ).data;
  },
  async getHealth() {
    return (await fetchEnvelope<ProjectionHealth>('/api/ledger/health')).data;
  },
  async getCommandCatalog() {
    return (await fetchEnvelope<CommandDefinition[]>('/api/meta/commands')).data;
  },
  async getActorProfiles() {
    return (await fetchEnvelope<ActorProfile[]>('/api/meta/actors')).data;
  },
  async getLogs(params) {
    return (
      await fetchEnvelope<PaginatedResult<ActivityLog>>(
        `/api/ops/logs${buildQuery({
          page: params?.page,
          page_size: params?.pageSize,
          level: params?.level,
          search: params?.search,
        })}`,
      )
    ).data;
  },
  async getRuntime() {
    return (await fetchEnvelope<RuntimeSnapshot>('/api/ops/runtime')).data;
  },
  async uploadDocument({ applicationId, documentType, fiscalYear, file }) {
    const authHeaders = await getAuthHeaders();
    const form = new FormData();
    form.append('application_id', applicationId);
    form.append('document_type', documentType);
    if (fiscalYear) form.append('fiscal_year', fiscalYear);
    form.append('file', file);
    const response = await fetch(`${apiBaseUrl}/api/uploads/documents`, {
      method: 'POST',
      headers: authHeaders,
      body: form,
    });
    return (await resolveEnvelope<DocumentUploadResult>(response)).data;
  },
  async invokeTool(toolName, payload = {}, idempotencyKey = crypto.randomUUID()) {
    const envelope = await fetchEnvelope<ToolExecutionResult>(`/api/tools/${encodeURIComponent(toolName)}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(payload),
    });
    return { result: envelope.data, meta: envelope.meta };
  },
};

export const ledgerClient: LedgerClient = apiClient;

export const ledgerApiConfig = {
  apiBaseUrl,
  useMock: false,
};
