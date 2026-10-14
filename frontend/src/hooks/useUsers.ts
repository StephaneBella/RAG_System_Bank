import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { queryKeys, usersApi } from '@/api';
import type { CreateUserRequest, UpdateUserRequest, UsersQuery } from '@/api/types';

import { useAuth } from '@/context/AuthContext';

export function useUsers(params: UsersQuery = {}, enabled = true) {
  const { isAuthenticated, isAdmin } = useAuth();
  return useQuery({
    queryKey: queryKeys.users.list(params),
    queryFn: () => usersApi.listUsers(params),
    enabled: isAuthenticated && isAdmin && enabled,
    placeholderData: keepPreviousData,
  });
}

function invalidateUsers(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.users.all });
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateUserRequest) => usersApi.createUser(payload),
    onSuccess: () => invalidateUsers(queryClient),
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: UpdateUserRequest }) =>
      usersApi.updateUser(id, payload),
    onSuccess: () => invalidateUsers(queryClient),
  });
}

export function useSetUserStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) =>
      usersApi.setUserStatus(id, active),
    onSuccess: () => invalidateUsers(queryClient),
  });
}
