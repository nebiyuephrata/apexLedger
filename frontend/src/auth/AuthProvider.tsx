import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { ClerkProvider, SignedIn, SignedOut, useAuth as useClerkAuth, useUser } from '@clerk/clerk-react';
import { registerAuthHeadersResolver } from '../platform/http';

export type Role =
  | 'loan_officer'
  | 'compliance_officer'
  | 'security_officer'
  | 'admin'
  | 'auditor'
  | 'applicant'
  | 'user_proxy';

export type AuthUser = {
  id: string;
  name: string;
  email: string;
  role: Role;
  orgId?: string | null;
  isInternal?: boolean;
};

type AuthContextValue = {
  user: AuthUser | null;
  setRole: (role: Role) => void;
  signOut?: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const mockUsers: AuthUser[] = [
  {
    id: 'u-loan-01',
    name: 'Amina Solomon',
    email: 'amina@ledger.local',
    role: 'loan_officer'
  },
  {
    id: 'u-comp-01',
    name: 'Jonas Okoro',
    email: 'jonas@ledger.local',
    role: 'compliance_officer'
  },
  {
    id: 'u-sec-01',
    name: 'Rhea Patel',
    email: 'rhea@ledger.local',
    role: 'security_officer'
  },
  {
    id: 'u-admin-01',
    name: 'Diego Chan',
    email: 'diego@ledger.local',
    role: 'admin'
  },
  {
    id: 'u-audit-01',
    name: 'Nia Bekele',
    email: 'nia@ledger.local',
    role: 'auditor'
  },
  {
    id: 'u-app-01',
    name: 'ACME Applicant',
    email: 'finance@acme.local',
    role: 'applicant'
  },
  {
    id: 'u-proxy-01',
    name: 'UserProxy',
    email: 'proxy@ledger.local',
    role: 'user_proxy'
  }
];

const MockAuthProvider: React.FC<React.PropsWithChildren> = ({ children }) => {
  const [role, setRole] = useState<Role>('loan_officer');

  const user = useMemo(() => {
    const selected = mockUsers.find((entry) => entry.role === role) ?? mockUsers[0];
    return {
      ...selected,
      role,
      orgId: import.meta.env.VITE_LEDGER_DEV_ORG_ID ?? 'org_demo',
      isInternal: import.meta.env.VITE_LEDGER_DEV_INTERNAL === 'true',
    };
  }, [role]);

  useEffect(() => {
    registerAuthHeadersResolver(async () => ({
      'X-Ledger-Dev-Role': role,
      'X-Ledger-Dev-Org-Id': import.meta.env.VITE_LEDGER_DEV_ORG_ID ?? 'org_demo',
      'X-Ledger-Dev-User-Id': user.id,
      'X-Ledger-Dev-Internal': String(import.meta.env.VITE_LEDGER_DEV_INTERNAL === 'true' || user.isInternal),
      'X-Ledger-Dev-Email': user.email,
      'X-Ledger-Dev-Name': user.name,
    }));
  }, [role, user]);

  return (
    <AuthContext.Provider value={{ user, setRole }}>
      {children}
    </AuthContext.Provider>
  );
};

const ClerkBridge: React.FC<React.PropsWithChildren> = ({ children }) => {
  const { user, isSignedIn } = useUser();
  const { getToken, orgId } = useClerkAuth();
  const [overrideRole, setOverrideRole] = useState<Role | null>(null);

  const contextValue = useMemo<AuthContextValue>(() => {
    if (!isSignedIn || !user) {
      return { user: null, setRole: () => {} };
    }

    const metaRole = user.publicMetadata?.role || user.unsafeMetadata?.role;
    const role = (overrideRole || metaRole || 'loan_officer') as Role;

    return {
      user: {
        id: user.id,
        name: user.fullName || user.username || 'Ledger User',
        email: user.primaryEmailAddress?.emailAddress || 'unknown',
        role,
        orgId: orgId ?? null,
        isInternal: role === 'admin' || role === 'security_officer' || role === 'auditor'
      },
      setRole: setOverrideRole
    };
  }, [isSignedIn, user, overrideRole, orgId]);

  useEffect(() => {
    registerAuthHeadersResolver(async () => {
      const token = await getToken();
      return token
        ? { Authorization: `Bearer ${token}` }
        : {};
    });
  }, [getToken]);

  return (
    <AuthContext.Provider value={contextValue}>
      <SignedOut>
        <div className="min-h-screen flex items-center justify-center">
          <div className="bg-slate-900/70 p-10 rounded-2xl text-center">
            <h2 className="text-2xl font-semibold mb-2">Sign in required</h2>
            <p className="text-slate-400">Connect Clerk to access the Ledger console.</p>
          </div>
        </div>
      </SignedOut>
      <SignedIn>{children}</SignedIn>
    </AuthContext.Provider>
  );
};

export const authProvider = (Wrapper: React.FC<React.PropsWithChildren>) => {
  const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;

  if (!publishableKey) {
    return function AuthRoot({ children }: React.PropsWithChildren) {
      return (
        <MockAuthProvider>
          <Wrapper>{children}</Wrapper>
        </MockAuthProvider>
      );
    };
  }

  return function AuthRoot({ children }: React.PropsWithChildren) {
    return (
      <ClerkProvider publishableKey={publishableKey}>
        <ClerkBridge>
          <Wrapper>{children}</Wrapper>
        </ClerkBridge>
      </ClerkProvider>
    );
  };
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
};
