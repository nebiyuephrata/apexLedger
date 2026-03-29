import React from 'react';
import { Button, Card, CardContent, Divider, TextField, Typography } from '@mui/material';
import toast from 'react-hot-toast';

export const WhatIf: React.FC = () => {
  return (
    <div className="space-y-6">
      <div>
        <Typography variant="h4" className="font-display">What-If Projector</Typography>
        <Typography variant="body2" className="text-slate-400">
          Run counterfactual scenarios without writing to the event store.
        </Typography>
      </div>

      <Card className="bg-slate-900/70 border border-slate-800">
        <CardContent className="space-y-4">
          <Typography variant="h6" className="font-display">Scenario Builder</Typography>
          <Divider className="border-slate-800" />
          <TextField label="Application ID" defaultValue="loan-ACME-0091" fullWidth />
          <TextField label="Branch Point" defaultValue="CreditAnalysisCompleted@pos:431" fullWidth />
          <TextField label="Injected Event" defaultValue="Risk tier => HIGH" fullWidth />
          <Button variant="contained" onClick={() => toast.success('What-if simulation queued')}>
            Run Simulation
          </Button>
        </CardContent>
      </Card>
    </div>
  );
};
