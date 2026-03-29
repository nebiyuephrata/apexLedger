import React from 'react';
import { Button, Chip, Typography } from '@mui/material';
import toast from 'react-hot-toast';
import { SectionCard } from '../components/SectionCard';
import { LagStatus } from '../components/LagStatus';
import { useAuditTrailQuery, useHealthQuery } from '../features/ledger/hooks';

export const Security: React.FC = () => {
  const { data: health = null } = useHealthQuery();
  const { data: auditPage } = useAuditTrailQuery('loan-ACME-0091');
  const audit = auditPage?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Typography variant="h4" className="font-display">
            Security and Governance
          </Typography>
          <Typography variant="body2" className="max-w-3xl text-slate-400">
            This screen emphasizes tamper evidence, outbox reliability, and the operational signals around projection lag and poison-pill handling.
          </Typography>
        </div>
        <LagStatus health={health} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <SectionCard
          title="Audit Ledger Timeline"
          subtitle="Cross-stream event evidence that a security or audit reviewer can validate against the integrity chain."
          actions={<Button variant="contained" onClick={() => toast.success('Integrity proof exported')}>Export Proof</Button>}
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
                  <Chip label={event.time} size="small" variant="outlined" />
                </div>
                <Typography variant="body2" className="mt-2 text-slate-300">
                  {event.detail}
                </Typography>
              </div>
            ))}
          </div>
        </SectionCard>

        <div className="space-y-4">
          <SectionCard
            title="Action Guards"
            subtitle="Human approval boundaries for maybe-irreversible commands."
          >
            <div className="space-y-3">
              <div className="flex items-center justify-between rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <Typography variant="body2">Detected high-risk actions</Typography>
                <Chip label="3" color="warning" />
              </div>
              <div className="flex items-center justify-between rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <Typography variant="body2">Escalated for human approval</Typography>
                <Chip label="1" color="info" />
              </div>
              <div className="flex items-center justify-between rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <Typography variant="body2">Resolved safely</Typography>
                <Chip label="2" color="success" />
              </div>
            </div>
          </SectionCard>

          <SectionCard
            title="Outbox and Daemon"
            subtitle="Delivery and projection control health."
          >
            <div className="space-y-4">
              <div>
                <Typography variant="caption" className="text-slate-400">
                  Outbox Delivery
                </Typography>
                <div className="mt-2 h-2 w-full rounded-full bg-slate-800">
                  <div className="h-2 w-[82%] rounded-full bg-emerald-400" />
                </div>
                <Typography variant="body2" className="mt-2 text-slate-300">
                  98.4% delivered, 4 pending, 1 retrying poison pill.
                </Typography>
              </div>
              <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <Typography variant="subtitle2">Checkpoint Contract</Typography>
                <Typography variant="body2" className="mt-2 text-slate-300">
                  Projection writes and `projection_checkpoints` updates must share the same transaction boundary.
                </Typography>
              </div>
            </div>
          </SectionCard>
        </div>
      </div>
    </div>
  );
};
