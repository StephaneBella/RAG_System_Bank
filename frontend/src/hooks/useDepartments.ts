import { useQuery } from '@tanstack/react-query';

import { departmentsApi, queryKeys } from '@/api';

import { useAuth } from '@/context/AuthContext';

export function useDepartments() {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: queryKeys.departments,
    queryFn: departmentsApi.fetchDepartments,
    enabled: isAuthenticated,
    staleTime: 10 * 60 * 1000,
  });
}
