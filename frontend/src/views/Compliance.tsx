import React from 'react';
import { Button, Chip, TextField, Typography } from '@mui/material';
import toast from 'react-hot-toast';
import { SectionCard } from '../components/SectionCard';
import { useAuditTrailQuery, useComplianceQuery, useInvokeToolMutation } from '../features/ledger/hooks';

export const Compliance: React.FC = () => {
  const [applicationId, setApplicationId] = React.useState('loan-ACME-0091');
  const [asOf, setAsOf] = React.useState('2026-03-25T09:10');
  const { data: snapshot = null, refetch: refetchSnapshot } = useComplianceQuery(applicationId, asOf);
  const { data: auditPage, refetch: refetchAudit } = useAuditTrailQuery(applicationId);
  const invokeTool = useInvokeToolMutation();
  const audit = auditPage?.items ?? [];
  const loadView = React.useCallback(async () => {
    await Promise.all([refetchSnapshot(), refetchAudit()]);
  }, [refetchAudit, refetchSnapshot]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Typography variant="h4" className="font-display">
            Compliance Audit View
          </Typography>
          <Typography variant="body2" className="max-w-3xl text-slate-400">
            This screen mirrors the `projection_compliance_audit` and `projection_compliance_snapshots` tables, with time-travel queries for regulatory review.
          </Typography>
        </div>
        <Button
          variant="contained"
          onClick={() =>
            invokeTool.mutate(
              { toolName: 'run_integrity_check', payload: { entity_id: applicationId, entity_type: 'application' } },
              {
                onSuccess: () => toast.success('Integrity check queued'),
                onError: (error) => toast.error(error instanceof Error ? error.message : 'Integrity check failed'),
              },
            )
          }
        >
          Run Integrity Check
        </Button>
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <SectionCard
          title="Temporal Query"
          subtitle="Reads the latest snapshot at or before the requested timestamp."
          actions={<Button variant="outlined" color="inherit" onClick={() => void loadView()}>Reload</Button>}
        >
          <div className="space-y-4">
            <TextField label="Application ID" value={applicationId} onChange={(event) => setApplicationId(event.target.value)} fullWidth />
            <TextField
              label="As Of"
              type="datetime-local"
              value={asOf}
              onChange={(event) => setAsOf(event.target.value)}
              InputLabelProps={{ shrink: true }}
              fullWidth
            />
            {snapshot ? (
              <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <div className="flex flex-wrap gap-2">
                  <Chip label={snapshot.overall_verdict ?? 'UNKNOWN'} color={snapshot.overall_verdict === 'BLOCKED' ? 'error' : 'success'} />
                  {snapshot.has_hard_block ? <Chip label="Hard Block" color="warning" variant="outlined" /> : null}
                  {snapshot.rule_id ? <Chip label={snapshot.rule_id} variant="outlined" /> : null}
                </div>
                <Typography variant="body2" className="mt-4 text-slate-300">
                  Event: {snapshot.event_type ?? 'n/a'}
                </Typography>
                <Typography variant="body2" className="mt-2 text-slate-300">
                  Rules passed: {(snapshot.rules_passed ?? []).join(', ') || 'none'}
                </Typography>
                <Typography variant="body2" className="mt-2 text-slate-300">
                  Rules failed: {(snapshot.rules_failed ?? []).join(', ') || 'none'}
                </Typography>
              </div>
            ) : (
              <Typography variant="body2" className="text-slate-400">
                No snapshot found for the selected timestamp.
              </Typography>
            )}
          </div>
        </SectionCard>

        <SectionCard
          title="Regulatory Trace"
          subtitle="Cross-check the compliance snapshot against the application audit trail."
        >
          <div className="space-y-3">
            {audit.map((event) => (
              <div key={`${event.stream_id}-${event.time}`} className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <Typography variant="subtitle2">{event.event_type}</Typography>
                    <Typography variant="caption" className="text-slate-400">
                      {event.stream_id}
                    </Typography>
                  </div>
                  <Chip label={`gp:${event.global_position ?? '-'}`} size="small" variant="outlined" />
                </div>
                <Typography variant="body2" className="mt-2 text-slate-300">
                  {event.detail}
                </Typography>
              </div>
            ))}
          </div>
        </SectionCard>
      </div>
    </div>
  );
};
