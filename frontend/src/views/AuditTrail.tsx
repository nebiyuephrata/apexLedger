import React from 'react';
import { Chip, Typography } from '@mui/material';
import { SectionCard } from '../components/SectionCard';
import { useAuditTrailQuery } from '../features/ledger/hooks';

export const AuditTrail: React.FC = () => {
  const { data: trail = [] } = useAuditTrailQuery('loan-ACME-0091');

  return (
    <div className="space-y-6">
      <div>
        <Typography variant="h4" className="font-display">
          Audit Trail
        </Typography>
        <Typography variant="body2" className="max-w-3xl text-slate-400">
          Auditors use this to inspect the immutable sequence that supports a decision, along with global positions that make independent replay possible.
        </Typography>
      </div>

      <SectionCard
        title="Application Timeline"
        subtitle="Cross-stream evidence from the append-only event log."
      >
        <div className="space-y-3">
          {trail.map((event) => (
            <div key={`${event.stream_id}-${event.global_position}`} className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <Typography variant="subtitle2">{event.event_type}</Typography>
                  <Typography variant="caption" className="text-slate-400">
                    {event.stream_id}
                  </Typography>
                </div>
                <div className="flex gap-2">
                  <Chip label={event.time} size="small" variant="outlined" />
                  <Chip label={`gp:${event.global_position ?? '-'}`} size="small" variant="outlined" />
                </div>
              </div>
              <Typography variant="body2" className="mt-2 text-slate-300">
                {event.detail}
              </Typography>
            </div>
          ))}
        </div>
      </SectionCard>
    </div>
  );
};
