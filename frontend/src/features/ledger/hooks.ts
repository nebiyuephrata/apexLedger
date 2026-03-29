import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ledgerClient } from '../../lib/ledgerClient';

export const ledgerKeys = {
  session: ['session'] as const,
  applications: ['applications'] as const,
  application: (applicationId: string) => ['application', applicationId] as const,
  compliance: (applicationId: string, asOf?: string) => ['compliance', applicationId, asOf ?? 'current'] as const,
  audit: (applicationId: string) => ['audit', applicationId] as const,
  health: ['health'] as const,
  commands: ['commands'] as const,
  actors: ['actors'] as const,
  sessions: ['agent-sessions'] as const,
  agentPerformance: (agentType?: string) => ['agent-performance', agentType ?? 'default'] as const,
  logs: (limit = 50) => ['logs', limit] as const,
};

export const useSessionQuery = () =>
  useQuery({
    queryKey: ledgerKeys.session,
    queryFn: () => ledgerClient.getSession(),
    staleTime: 15_000,
  });

export const useApplicationsQuery = () =>
  useQuery({
    queryKey: ledgerKeys.applications,
    queryFn: () => ledgerClient.getApplicationSummaries(),
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

export const useAuditTrailQuery = (applicationId: string) =>
  useQuery({
    queryKey: ledgerKeys.audit(applicationId),
    queryFn: () => ledgerClient.getAuditTrail(applicationId),
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

export const useAgentSessionsQuery = () =>
  useQuery({
    queryKey: ledgerKeys.sessions,
    queryFn: () => ledgerClient.getAgentSessions(),
    staleTime: 5_000,
    refetchInterval: 10_000,
  });

export const useAgentPerformanceQuery = (agentType?: string) =>
  useQuery({
    queryKey: ledgerKeys.agentPerformance(agentType),
    queryFn: () => ledgerClient.getAgentPerformance(agentType),
    staleTime: 10_000,
  });

export const useLogsQuery = (limit = 50) =>
  useQuery({
    queryKey: ledgerKeys.logs(limit),
    queryFn: () => ledgerClient.getLogs(limit),
    staleTime: 3_000,
    refetchInterval: 10_000,
  });

export const useInvokeToolMutation = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ toolName, payload, idempotencyKey }: { toolName: string; payload?: Record<string, unknown>; idempotencyKey?: string }) =>
      ledgerClient.invokeTool(toolName, payload, idempotencyKey),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ledgerKeys.applications }),
        queryClient.invalidateQueries({ queryKey: ledgerKeys.health }),
        queryClient.invalidateQueries({ queryKey: ledgerKeys.sessions }),
        queryClient.invalidateQueries({ queryKey: ledgerKeys.logs() }),
      ]);
    },
  });
};
