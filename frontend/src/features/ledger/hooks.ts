import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ledgerClient } from '../../lib/ledgerClient';

export const ledgerKeys = {
  session: ['session'] as const,
  applications: (params?: { page?: number; pageSize?: number; search?: string; state?: string }) => ['applications', params ?? {}] as const,
  application: (applicationId: string) => ['application', applicationId] as const,
  compliance: (applicationId: string, asOf?: string) => ['compliance', applicationId, asOf ?? 'current'] as const,
  audit: (applicationId: string, params?: { page?: number; pageSize?: number; eventType?: string }) => ['audit', applicationId, params ?? {}] as const,
  health: ['health'] as const,
  commands: ['commands'] as const,
  actors: ['actors'] as const,
  sessions: (params?: { page?: number; pageSize?: number; agentType?: string }) => ['agent-sessions', params ?? {}] as const,
  agentPerformance: (agentType?: string) => ['agent-performance', agentType ?? 'default'] as const,
  logs: (params?: { page?: number; pageSize?: number; level?: string; search?: string }) => ['logs', params ?? {}] as const,
  runtime: ['runtime'] as const,
};

export const useSessionQuery = () =>
  useQuery({
    queryKey: ledgerKeys.session,
    queryFn: () => ledgerClient.getSession(),
    staleTime: 15_000,
  });

export const useApplicationsQuery = (params?: { page?: number; pageSize?: number; search?: string; state?: string }) =>
  useQuery({
    queryKey: ledgerKeys.applications(params),
    queryFn: () => ledgerClient.getApplicationSummaries(params),
    staleTime: 5_000,
  });

export const useApplicationQuery = (applicationId: string) =>
  useQuery({
    queryKey: ledgerKeys.application(applicationId),
    queryFn: () => ledgerClient.getApplicationSummary(applicationId),
    enabled: Boolean(applicationId),
    staleTime: 5_000,
  });

export const useComplianceQuery = (applicationId: string, asOf?: string) =>
  useQuery({
    queryKey: ledgerKeys.compliance(applicationId, asOf),
    queryFn: () => ledgerClient.getComplianceView(applicationId, asOf),
    enabled: Boolean(applicationId),
    staleTime: 15_000,
  });

export const useAuditTrailQuery = (applicationId: string, params?: { page?: number; pageSize?: number; eventType?: string }) =>
  useQuery({
    queryKey: ledgerKeys.audit(applicationId, params),
    queryFn: () => ledgerClient.getAuditTrail(applicationId, params),
    enabled: Boolean(applicationId),
    staleTime: 10_000,
  });

export const useHealthQuery = () =>
  useQuery({
    queryKey: ledgerKeys.health,
    queryFn: () => ledgerClient.getHealth(),
    staleTime: 3_000,
    refetchInterval: 5_000,
  });

export const useCommandCatalogQuery = () =>
  useQuery({
    queryKey: ledgerKeys.commands,
    queryFn: () => ledgerClient.getCommandCatalog(),
    staleTime: 60_000,
  });

export const useActorProfilesQuery = () =>
  useQuery({
    queryKey: ledgerKeys.actors,
    queryFn: () => ledgerClient.getActorProfiles(),
    staleTime: 60_000,
  });

export const useAgentSessionsQuery = (params?: { page?: number; pageSize?: number; agentType?: string }) =>
  useQuery({
    queryKey: ledgerKeys.sessions(params),
    queryFn: () => ledgerClient.getAgentSessions(params),
    staleTime: 5_000,
    refetchInterval: 10_000,
  });

export const useAgentPerformanceQuery = (agentType?: string) =>
  useQuery({
    queryKey: ledgerKeys.agentPerformance(agentType),
    queryFn: () => ledgerClient.getAgentPerformance(agentType),
    staleTime: 10_000,
  });

export const useLogsQuery = (params?: { page?: number; pageSize?: number; level?: string; search?: string }) =>
  useQuery({
    queryKey: ledgerKeys.logs(params),
    queryFn: () => ledgerClient.getLogs(params),
    staleTime: 3_000,
    refetchInterval: 10_000,
  });

export const useRuntimeQuery = () =>
  useQuery({
    queryKey: ledgerKeys.runtime,
    queryFn: () => ledgerClient.getRuntime(),
    staleTime: 5_000,
    refetchInterval: 10_000,
  });

export const useInvokeToolMutation = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ toolName, payload, idempotencyKey }: { toolName: string; payload?: Record<string, unknown>; idempotencyKey?: string }) =>
      ledgerClient.invokeTool(toolName, payload, idempotencyKey),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['applications'] }),
        queryClient.invalidateQueries({ queryKey: ledgerKeys.health }),
        queryClient.invalidateQueries({ queryKey: ['agent-sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['logs'] }),
        queryClient.invalidateQueries({ queryKey: ledgerKeys.runtime }),
      ]);
    },
  });
};
