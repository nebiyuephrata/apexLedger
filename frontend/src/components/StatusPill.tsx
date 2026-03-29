import React from 'react';
import { Chip } from '@mui/material';

type Props = {
  label: string;
  tone?: 'success' | 'warning' | 'error' | 'info';
};

export const StatusPill: React.FC<Props> = ({ label, tone = 'info' }) => {
  return <Chip label={label} color={tone} size="small" />;
};
