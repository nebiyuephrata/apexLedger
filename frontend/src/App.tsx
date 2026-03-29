import React, { Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './layout/AppShell';
import { ProtectedRoute } from './routes/ProtectedRoute';
import { RoleRoute } from './routes/RoleRoute';
import { useAuth } from './auth/AuthProvider';

const Dashboard = React.lazy(async () => import('./views/Dashboard').then((mod) => ({ default: mod.Dashboard })));
const Compliance = React.lazy(async () => import('./views/Compliance').then((mod) => ({ default: mod.Compliance })));
const Security = React.lazy(async () => import('./views/Security').then((mod) => ({ default: mod.Security })));
const Admin = React.lazy(async () => import('./views/Admin').then((mod) => ({ default: mod.Admin })));
const Logs = React.lazy(async () => import('./views/Logs').then((mod) => ({ default: mod.Logs })));
const WhatIf = React.lazy(async () => import('./views/WhatIf').then((mod) => ({ default: mod.WhatIf })));
const Login = React.lazy(async () => import('./views/Login').then((mod) => ({ default: mod.Login })));
const Unauthorized = React.lazy(async () => import('./views/Unauthorized').then((mod) => ({ default: mod.Unauthorized })));
const AuditTrail = React.lazy(async () => import('./views/AuditTrail').then((mod) => ({ default: mod.AuditTrail })));
const Applicant = React.lazy(async () => import('./views/Applicant').then((mod) => ({ default: mod.Applicant })));

const HomeRedirect: React.FC = () => {
  const { user } = useAuth();

  const target =
    user?.role === 'applicant'
      ? '/applicant'
      : user?.role === 'compliance_officer'
        ? '/compliance'
        : user?.role === 'security_officer'
          ? '/security'
          : user?.role === 'auditor'
            ? '/audit-trail'
            : '/dashboard';

  return <Navigate to={target} replace />;
};

const App: React.FC = () => {
  return (
    <Suspense fallback={<div className="flex min-h-screen items-center justify-center text-slate-400">Loading Ledger console...</div>}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/unauthorized" element={<Unauthorized />} />
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <AppShell>
                <Routes>
                  <Route path="/" element={<HomeRedirect />} />
                  <Route
                    path="/dashboard"
                    element={
                      <RoleRoute allowed={['loan_officer', 'admin', 'user_proxy']} view="dashboard">
                        <Dashboard />
                      </RoleRoute>
                    }
                  />
                  <Route
                    path="/compliance"
                    element={
                      <RoleRoute allowed={['compliance_officer', 'auditor', 'admin']} view="compliance">
                        <Compliance />
                      </RoleRoute>
                    }
                  />
                  <Route
                    path="/audit-trail"
                    element={
                      <RoleRoute allowed={['auditor', 'security_officer', 'admin']} view="audit-trail">
                        <AuditTrail />
                      </RoleRoute>
                    }
                  />
                  <Route
                    path="/security"
                    element={
                      <RoleRoute allowed={['security_officer', 'admin']} view="security">
                        <Security />
                      </RoleRoute>
                    }
                  />
                  <Route
                    path="/admin"
                    element={
                      <RoleRoute allowed={['admin']} view="admin">
                        <Admin />
                      </RoleRoute>
                    }
                  />
                  <Route
                    path="/logs"
                    element={
                      <RoleRoute allowed={['security_officer', 'admin']} view="logs">
                        <Logs />
                      </RoleRoute>
                    }
                  />
                  <Route
                    path="/what-if"
                    element={
                      <RoleRoute allowed={['admin', 'auditor']} view="what-if">
                        <WhatIf />
                      </RoleRoute>
                    }
                  />
                  <Route
                    path="/applicant"
                    element={
                      <RoleRoute allowed={['applicant']} view="applicant">
                        <Applicant />
                      </RoleRoute>
                    }
                  />
                  <Route path="*" element={<HomeRedirect />} />
                </Routes>
              </AppShell>
            </ProtectedRoute>
          }
        />
      </Routes>
    </Suspense>
  );
};

export default App;
