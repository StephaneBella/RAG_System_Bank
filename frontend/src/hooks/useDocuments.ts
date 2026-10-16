import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { documentsApi, queryKeys } from '@/api';
import type { DocumentsQuery, SearchDocumentsQuery } from '@/api/types';

import { useAuth } from '@/context/AuthContext';

export function useDocuments(params: DocumentsQuery = {}, enabled = true) {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: queryKeys.documents.list(params),
    queryFn: () => documentsApi.listDocuments(params),
    enabled: isAuthenticated && enabled,
    placeholderData: keepPreviousData,
  });
}

export function useSearchDocuments(params: SearchDocumentsQuery, enabled = true) {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: queryKeys.documents.search(params),
    queryFn: () => documentsApi.searchDocuments(params),
    enabled: isAuthenticated && enabled,
    placeholderData: keepPreviousData,
  });
}

export function useDocument(id: number | undefined) {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: queryKeys.documents.detail(id ?? 0),
    queryFn: () => documentsApi.getDocument(id as number),
    enabled: isAuthenticated && typeof id === 'number' && Number.isFinite(id),
  });
}

export function useDocumentContent(id: number | undefined, enabled = true) {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: queryKeys.documents.content(id ?? 0),
    queryFn: () => documentsApi.getDocumentContent(id as number),
    enabled: isAuthenticated && enabled && typeof id === 'number' && Number.isFinite(id),
    staleTime: Infinity,
  });
}

function invalidateDocuments(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
}

export function useCreateDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: documentsApi.createDocument,
    onSuccess: () => invalidateDocuments(queryClient),
  });
}

export function useUpdateDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, values }: { id: number; values: Parameters<typeof documentsApi.updateDocument>[1] }) =>
      documentsApi.updateDocument(id, values),
    onSuccess: () => invalidateDocuments(queryClient),
  });
}

export function useDeleteDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: documentsApi.deleteDocument,
    onSuccess: () => invalidateDocuments(queryClient),
  });
}
