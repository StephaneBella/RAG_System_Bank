import type { AuditLog, Department, DocumentItem, User, UserRole, UserStatus } from '@/api/types';

const now = Date.now();
const iso = (daysAgo: number, hoursAgo = 0): string =>
  new Date(now - daysAgo * 86_400_000 - hoursAgo * 3_600_000).toISOString();

export interface MockUser extends User {
  password: string;
}

export const departments: Department[] = [
  { id: 1, name: 'Direction Générale' },
  { id: 2, name: 'Conformité & Risques' },
  { id: 3, name: 'Direction Juridique' },
  { id: 4, name: 'Finances & Comptabilité' },
  { id: 5, name: 'Ressources Humaines' },
];

export const users: MockUser[] = [
  {
    id: 1,
    email: 'sophie.moreau@banque-institution.fr',
    password: 'Password123',
    role: 'ADMIN',
    status: 'ACTIVE',
    department_id: 1,
    department_name: 'Direction Générale',
  },
  {
    id: 2,
    email: 'alexandre.laurent@banque-institution.fr',
    password: 'Password123',
    role: 'EMPLOYEE',
    status: 'ACTIVE',
    department_id: 2,
    department_name: 'Conformité & Risques',
  },
  {
    id: 3,
    email: 'claire.dubois@banque-institution.fr',
    password: 'Password123',
    role: 'EMPLOYEE',
    status: 'ACTIVE',
    department_id: 3,
    department_name: 'Direction Juridique',
  },
  {
    id: 4,
    email: 'marc.bernard@banque-institution.fr',
    password: 'Password123',
    role: 'EMPLOYEE',
    status: 'INACTIVE',
    department_id: 4,
    department_name: 'Finances & Comptabilité',
  },
  {
    id: 5,
    email: 'helene.petit@banque-institution.fr',
    password: 'Password123',
    role: 'EMPLOYEE',
    status: 'ACTIVE',
    department_id: 5,
    department_name: 'Ressources Humaines',
  },
];

let documentIds = 0;
const makeDocument = (doc: Omit<DocumentItem, 'id'>): DocumentItem => {
  documentIds += 1;
  return { id: documentIds, ...doc };
};

export const documents: DocumentItem[] = [
  makeDocument({
    title: "Procédure d'audit LCB-FT v4.2",
    department_id: 2,
    department_name: 'Conformité & Risques',
    classification: 'confidential',
    file_name: 'private__conformite_&_risques__procedure_audit_lcb-ft_v4.2.pdf',
    file_size: 3_355_443,
    mime_type: 'application/pdf',
    description: 'Comptes résidents fiscaux UE — Réf. ACPR-LCBFT-2024-V4.2',
    created_at: iso(2, 5),
    updated_at: iso(1, 2),
  }),
  makeDocument({
    title: 'Rapport de clôture semestrielle H1',
    department_id: 4,
    department_name: 'Finances & Comptabilité',
    classification: 'confidential',
    file_name: 'private__finances_&_comptabilite__rapport_cloture_semestrielle_h1.pdf',
    file_size: 8_178_892,
    mime_type: 'application/pdf',
    description: 'Audit financier certifié',
    created_at: iso(5, 3),
    updated_at: iso(5, 3),
  }),
  makeDocument({
    title: "Convention d'arbitrage et gouvernance bancaire",
    department_id: 3,
    department_name: 'Direction Juridique',
    classification: 'private',
    file_name: 'private__direction_juridique__convention_arbitrage_gouvernance.pdf',
    file_size: 1_992_294,
    mime_type: 'application/pdf',
    description: "Conseil d'administration",
    created_at: iso(9, 1),
    updated_at: iso(3, 6),
  }),
  makeDocument({
    title: 'Politique de confidentialité et RGPD collaborateurs',
    department_id: 5,
    department_name: 'Ressources Humaines',
    classification: 'private',
    file_name: 'public__ressources_humaines__politique_confidentialite_rgpd.pdf',
    file_size: 2_516_582,
    mime_type: 'application/pdf',
    description: "Accord d'entreprise 2024",
    created_at: iso(12, 4),
    updated_at: iso(12, 4),
  }),
  makeDocument({
    title: "Plan de Continuité d'Activité (PCA) Groupe",
    department_id: 2,
    department_name: 'Conformité & Risques',
    classification: 'confidential',
    file_name: 'private__conformite_&_risques__plan_continuite_activite_pca.pdf',
    file_size: 12_058_624,
    mime_type: 'application/pdf',
    description: 'Mise à jour annuelle',
    created_at: iso(18, 7),
    updated_at: iso(18, 7),
  }),
  makeDocument({
    title: 'Guide opérationnel des transferts SEPA & SWIFT',
    department_id: 4,
    department_name: 'Finances & Comptabilité',
    classification: 'public',
    file_name: 'public__finances_&_comptabilite__guide_transferts_sepa_swift.pdf',
    file_size: 1_048_576,
    mime_type: 'application/pdf',
    description: 'Procédures de paiement international',
    created_at: iso(25, 2),
    updated_at: iso(20, 8),
  }),
  makeDocument({
    title: 'Mandat de gestion sous mandat discrétionnaire',
    department_id: 3,
    department_name: 'Direction Juridique',
    classification: 'confidential',
    file_name: 'private__direction_juridique__mandat_gestion_discretionnaire.pdf',
    file_size: 892_928,
    mime_type: 'application/pdf',
    description: 'Modèle contractuel — remplacé en 2025',
    created_at: iso(40, 6),
    updated_at: iso(30, 6),
  }),
];

let auditIds = 0;
const makeAudit = (log: Omit<AuditLog, 'id'>): AuditLog => {
  auditIds += 1;
  return { id: auditIds, ...log };
};

const userById = (id: number): MockUser => users.find((u) => u.id === id)!;

const auditFor = (
  userId: number,
  action: AuditLog['action'],
  resourceType: AuditLog['resource_type'],
  resourceId: number,
  resourceLabel: string,
  result: AuditLog['result'],
  daysAgo: number,
  hoursAgo: number,
  ip: string,
  context: string,
): AuditLog =>
  makeAudit({
    user_id: userId,
    user_email: userById(userId).email,
    action,
    resource_type: resourceType,
    resource_id: resourceId,
    resource_label: resourceLabel,
    result,
    ip_address: ip,
    context,
    timestamp: iso(daysAgo, hoursAgo),
  });

export const auditLogs: AuditLog[] = [
  auditFor(1, 'DOCUMENT_VIEW', 'DOCUMENT', 1, "Procédure d'audit LCB-FT v4.2", 'SUCCESS', 0, 5, '192.168.10.45', 'Siège Paris (VPN Sécurisé)'),
  auditFor(2, 'DOCUMENT_UPDATE', 'DOCUMENT', 5, "Plan de Continuité d'Activité (PCA) Groupe", 'SUCCESS', 0, 9, '192.168.10.88', 'Siège Paris (LAN Interne)'),
  auditFor(4, 'LOGIN', 'SESSION', 0, 'Portail — Authentification 2FA', 'FAILURE', 1, 3, '82.124.91.14', 'Inconnue — MFA (Compte verrouillé)'),
  auditFor(3, 'DOCUMENT_CREATE', 'DOCUMENT', 3, "Convention d'arbitrage et gouvernance bancaire", 'SUCCESS', 3, 1, '192.168.10.12', 'Siège Paris (LAN Interne)'),
  auditFor(1, 'DOCUMENT_DELETE', 'DOCUMENT', 99, 'Brouillon Procès-verbal CA 2023 [Archivé]', 'SUCCESS', 6, 2, '192.168.10.45', 'Siège Paris (VPN Sécurisé)'),
  auditFor(2, 'LOGIN', 'SESSION', 0, 'Session SSO Banque Privée', 'SUCCESS', 8, 4, '192.168.10.88', 'Siège Paris (LAN Interne)'),
  auditFor(5, 'DOCUMENT_VIEW', 'DOCUMENT', 4, 'Politique de confidentialité et RGPD collaborateurs', 'SUCCESS', 10, 7, '192.168.10.61', 'Siège Paris (LAN Interne)'),
  auditFor(1, 'USER_STATUS_UPDATE', 'USER', 4, 'marc.bernard@banque-institution.fr', 'SUCCESS', 11, 8, '192.168.10.45', 'Siège Paris (VPN Sécurisé)'),
  auditFor(2, 'DOCUMENT_VIEW', 'DOCUMENT', 2, 'Rapport de clôture semestrielle H1', 'FAILURE', 13, 2, '192.168.10.88', 'Accès refusé — Hors département'),
  auditFor(1, 'EXPORT', 'DOCUMENT', 0, 'Export du journal d’audit (CSV)', 'SUCCESS', 15, 5, '192.168.10.45', 'Siège Paris (VPN Sécurisé)'),
];

export const mockDb = {
  departments,
  users,
  documents,
  auditLogs,
  nextDocumentId: () => {
    documentIds += 1;
    return documentIds;
  },
  nextAuditId: () => {
    auditIds += 1;
    return auditIds;
  },
};

export function publicUser(user: MockUser): User {
  const { password: _password, ...rest } = user;
  return rest;
}

export const roleFor = (id: number): UserRole => userById(id).role;
export const statusFor = (id: number): UserStatus => userById(id).status;
