import React from 'react';
import { Card, CardContent, Typography } from '@mui/material';

type Props = {
  label: string;
  value: string;
  helper?: string;
};

export const MetricCard: React.FC<Props> = ({ label, value, helper }) => (
  <Card className="bg-slate-900/70 border border-slate-800">
    <CardContent>
      <Typography variant="overline" className="text-slate-400">
        {label}
      </Typography>
      <Typography variant="h5" className="font-display text-slate-100">
        {value}
      </Typography>
      {helper && (
        <Typography variant="body2" className="text-slate-400 mt-2">
          {helper}
        </Typography>
      )}
    </CardContent>
  </Card>
);
