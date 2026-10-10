import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { auditApi, queryKeys } from '@/api';
import type { AuditQuery } from '@/api/types';

import { useAuth } from '@/context/AuthContext';

export function useAudit(params: AuditQuery = {}, enabled = true) {
  const { isAuthenticated, isAdmin } = useAuth();
  return useQuery({
    queryKey: queryKeys.audit.list(params),
    queryFn: () => auditApi.listAudit(params),
    enabled: isAuthenticated && isAdmin && enabled,
    placeholderData: keepPreviousData,
  });
}
