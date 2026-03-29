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

type LocalSignInPayload = {
  role: Role;
  password: string;
  orgId?: string;
};

type AuthContextValue = {
  user: AuthUser | null;
  setRole: (role: Role) => void;
  signIn?: (payload: LocalSignInPayload) => Promise<void>;
  signOut?: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const localUsers: Record<Role, AuthUser> = {
  loan_officer: { id: 'u-loan-01', name: 'Amina Solomon', email: 'amina@ledger.local', role: 'loan_officer' },
  compliance_officer: { id: 'u-comp-01', name: 'Jonas Okoro', email: 'jonas@ledger.local', role: 'compliance_officer' },
  security_officer: { id: 'u-sec-01', name: 'Rhea Patel', email: 'rhea@ledger.local', role: 'security_officer', isInternal: true },
  admin: { id: 'u-admin-01', name: 'Diego Chan', email: 'diego@ledger.local', role: 'admin', isInternal: true },
  auditor: { id: 'u-audit-01', name: 'Nia Bekele', email: 'nia@ledger.local', role: 'auditor', isInternal: true },
  applicant: { id: 'u-app-01', name: 'ACME Applicant', email: 'finance@acme.local', role: 'applicant' },
  user_proxy: { id: 'u-proxy-01', name: 'UserProxy', email: 'proxy@ledger.local', role: 'user_proxy', isInternal: true },
};

const DEV_SESSION_KEY = 'ledger-dev-session';

const DevAuthProvider: React.FC<React.PropsWithChildren> = ({ children }) => {
  const [user, setUser] = useState<AuthUser | null>(() => {
    const raw = window.localStorage.getItem(DEV_SESSION_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  });

  const setRole = (role: Role) => {
    setUser((current) => {
      if (!current) return current;
      const next = { ...localUsers[role], orgId: current.orgId ?? 'org_demo' };
      window.localStorage.setItem(DEV_SESSION_KEY, JSON.stringify(next));
      return next;
    });
  };

  const signIn = async ({ role, password, orgId }: LocalSignInPayload) => {
    const expected = import.meta.env.VITE_LEDGER_DEV_PASSWORD ?? 'ledger-demo';
    if (password !== expected) {
      throw new Error('The local development password is incorrect.');
    }
    const nextUser = { ...localUsers[role], orgId: orgId || import.meta.env.VITE_LEDGER_DEV_ORG_ID || 'org_demo' };
    window.localStorage.setItem(DEV_SESSION_KEY, JSON.stringify(nextUser));
    setUser(nextUser);
  };

  const signOut = () => {
    window.localStorage.removeItem(DEV_SESSION_KEY);
    setUser(null);
  };

  useEffect(() => {
    registerAuthHeadersResolver(async () => {
      if (!user) return {};
      return {
        'X-Ledger-Dev-Role': user.role,
        'X-Ledger-Dev-Org-Id': user.orgId ?? 'org_demo',
        'X-Ledger-Dev-User-Id': user.id,
        'X-Ledger-Dev-Internal': String(Boolean(user.isInternal)),
        'X-Ledger-Dev-Email': user.email,
        'X-Ledger-Dev-Name': user.name,
      };
    });
  }, [user]);

  return (
    <AuthContext.Provider value={{ user, setRole, signIn, signOut }}>
      {children}
    </AuthContext.Provider>
  );
};

const ClerkBridge: React.FC<React.PropsWithChildren> = ({ children }) => {
  const { user, isSignedIn } = useUser();
  const { getToken, orgId, signOut } = useClerkAuth();
  const [overrideRole, setOverrideRole] = useState<Role | null>(null);

  const contextValue = useMemo<AuthContextValue>(() => {
    if (!isSignedIn || !user) {
      return { user: null, setRole: () => {}, signOut };
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
        isInternal: role === 'admin' || role === 'security_officer' || role === 'auditor' || role === 'user_proxy',
      },
      setRole: setOverrideRole,
      signOut: () => {
        void signOut();
      },
    };
  }, [isSignedIn, user, overrideRole, orgId, signOut]);

  useEffect(() => {
    registerAuthHeadersResolver(async () => {
      const token = await getToken();
      return token ? { Authorization: `Bearer ${token}` } : {};
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
        <DevAuthProvider>
          <Wrapper>{children}</Wrapper>
        </DevAuthProvider>
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
