import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

import { FullPageLoader } from '@/components/layout/FullPageLoader';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { landingPathFor, useAuth } from '@/context/AuthContext';
import { DocumentDetailPage } from '@/pages/DocumentDetailPage';
import { ForbiddenPage } from '@/pages/ForbiddenPage';
import { LibraryPage } from '@/pages/LibraryPage';
import { LoginPage } from '@/pages/LoginPage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { AdminAuditPage } from '@/pages/admin/AdminAuditPage';
import { AdminDocumentsPage } from '@/pages/admin/AdminDocumentsPage';
import { AdminUsersPage } from '@/pages/admin/AdminUsersPage';
import { ProtectedRoute } from '@/routes/ProtectedRoute';

function RootRedirect(): JSX.Element {
  const { isAuthenticated, isLoading, user } = useAuth();
  if (isLoading) return <FullPageLoader />;
  if (isAuthenticated && user) return <Navigate to={landingPathFor(user)} replace />;
  return <Navigate to="/login" replace />;
}

export function App(): JSX.Element {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route
          path="/library"
          element={
            <ProtectedRoute>
              <LibraryPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/documents/:id"
          element={
            <ProtectedRoute>
              <DocumentDetailPage />
            </ProtectedRoute>
          }
        />

        <Route
          element={
            <ProtectedRoute role="ADMIN">
              <AdminLayout />
            </ProtectedRoute>
          }
        >
          <Route path="/admin" element={<Navigate to="/library" replace />} />
          <Route path="/admin/documents" element={<AdminDocumentsPage />} />
          <Route path="/admin/users" element={<AdminUsersPage />} />
          <Route path="/admin/audit" element={<AdminAuditPage />} />
        </Route>

        <Route path="/" element={<RootRedirect />} />
        <Route path="/403" element={<ForbiddenPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </BrowserRouter>
  );
}
