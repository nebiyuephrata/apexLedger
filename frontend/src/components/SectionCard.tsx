import React from 'react';
import { Card, CardContent, Divider, Typography } from '@mui/material';

type SectionCardProps = React.PropsWithChildren<{
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  className?: string;
}>;

export const SectionCard: React.FC<SectionCardProps> = ({ title, subtitle, actions, className, children }) => {
  return (
    <Card className={`border border-slate-800 bg-slate-900/70 ${className ?? ''}`.trim()}>
      <CardContent>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <Typography variant="h6" className="font-display">
              {title}
            </Typography>
            {subtitle ? (
              <Typography variant="body2" className="text-slate-400">
                {subtitle}
              </Typography>
            ) : null}
          </div>
          {actions}
        </div>
        <Divider className="my-4 border-slate-800" />
        {children}
      </CardContent>
    </Card>
  );
};
