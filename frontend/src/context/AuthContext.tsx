/* eslint-disable react-refresh/only-export-components */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { authApi, clearToken, getToken, queryKeys, setToken, setUnauthorizedHandler } from '@/api';
import type { CurrentUserResponse, UserRole } from '@/api/types';
import { isTokenExpired } from '@/lib/utils';

interface AuthContextValue {
  user: CurrentUserResponse | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  isLoading: boolean;
  sessionExpired: boolean;
  login: (email: string, password: string) => Promise<CurrentUserResponse>;
  logout: () => Promise<void>;
  hasRole: (role: UserRole) => boolean;
  clearSessionExpired: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function landingPathFor(_user: Pick<CurrentUserResponse, 'role'>): string {
  return '/library';
}

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const queryClient = useQueryClient();
  const [hasToken, setHasToken] = useState<boolean>(() => Boolean(getToken()));
  const [sessionExpired, setSessionExpired] = useState(false);

  // Drop an already-expired token before the first request goes out.
  useEffect(() => {
    const token = getToken();
    if (token && isTokenExpired(token)) {
      clearToken();
      setHasToken(false);
      setSessionExpired(true);
    }
  }, []);

  // React to any 401 raised by the axios interceptor.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setHasToken(false);
      setSessionExpired(true);
      queryClient.removeQueries({ queryKey: queryKeys.currentUser });
    });
    return () => setUnauthorizedHandler(null);
  }, [queryClient]);

  const meQuery = useQuery({
    queryKey: queryKeys.currentUser,
    queryFn: authApi.fetchCurrentUser,
    enabled: hasToken,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  const user = meQuery.data ?? null;

  const login = useCallback(
    async (email: string, password: string): Promise<CurrentUserResponse> => {
      const token = await authApi.login({ email, password });
      setToken(token.access_token);
      setHasToken(true);
      setSessionExpired(false);
      const currentUser = await queryClient.fetchQuery({
        queryKey: queryKeys.currentUser,
        queryFn: authApi.fetchCurrentUser,
      });
      return currentUser;
    },
    [queryClient],
  );

  const logout = useCallback(async (): Promise<void> => {
    try {
      await authApi.logout();
    } catch {
      /* logout is best-effort */
    }
    clearToken();
    setHasToken(false);
    queryClient.clear();
  }, [queryClient]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isAuthenticated: Boolean(user),
      isAdmin: user?.role === 'ADMIN',
      isLoading: hasToken && meQuery.isLoading,
      sessionExpired,
      login,
      logout,
      hasRole: (role) => user?.role === role,
      clearSessionExpired: () => setSessionExpired(false),
    }),
    [user, hasToken, meQuery.isLoading, sessionExpired, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
