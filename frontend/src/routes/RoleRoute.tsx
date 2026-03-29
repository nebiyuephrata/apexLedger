import React from 'react';
import { Navigate } from 'react-router-dom';
import { Role, useAuth } from '../auth/AuthProvider';
import { useSessionQuery } from '../features/ledger/hooks';

type Props = {
  allowed: Role[];
  view?: string;
  children: React.ReactNode;
};

export const RoleRoute: React.FC<Props> = ({ allowed, view, children }) => {
  const { user } = useAuth();
  const { data: session, isLoading } = useSessionQuery();
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  if (isLoading) {
    return <div className="flex min-h-[40vh] items-center justify-center text-slate-400">Loading access policy...</div>;
  }
  if (view && session?.allowed_views?.length && !session.allowed_views.includes(view) && !session.is_internal) {
    return <Navigate to="/unauthorized" replace />;
  }
  if (!allowed.includes(user.role)) {
    return <Navigate to="/unauthorized" replace />;
  }
  return <>{children}</>;
};
