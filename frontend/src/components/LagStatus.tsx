import React from 'react';
import { Chip, Stack, Typography } from '@mui/material';
import { ProjectionHealth } from '../types/ledger';

type Props = {
  health: ProjectionHealth | null;
};

const toneForLag = (value: number) => {
  if (value <= 500) return 'success';
  if (value <= 2000) return 'warning';
  return 'error';
};

export const LagStatus: React.FC<Props> = ({ health }) => {
  if (!health) {
    return (
      <Typography variant="body2" className="text-slate-400">
        Loading projection lag...
      </Typography>
    );
  }

  return (
    <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5}>
      {Object.entries(health.lags).map(([name, lag]) => (
        <Chip
          key={name}
          label={`${name}: ${lag.lag_ms}ms`}
          color={toneForLag(lag.lag_ms)}
          variant="outlined"
        />
      ))}
    </Stack>
  );
};
