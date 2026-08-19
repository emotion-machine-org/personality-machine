'use client';

import { createContext, useContext, ReactNode, useMemo } from 'react';

type UserContextValue = {
  id: string;
  email: string | null;
  fullName: string | null;
  imageUrl: string | null;
};

const UserContext = createContext<UserContextValue | null>(null);

export function UserProvider({ user, children }: { user: UserContextValue; children: ReactNode }) {
  const value = useMemo(() => user, [user]);
  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
}

export function useCurrentUser() {
  const ctx = useContext(UserContext);
  if (!ctx) {
    throw new Error('useCurrentUser must be used within a UserProvider');
  }
  return ctx;
}
