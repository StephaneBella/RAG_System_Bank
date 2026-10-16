import { useTranslation } from 'react-i18next';

import type { InvitationCreated } from '@/api/types';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/lib/utils';

interface InvitationLinkModalProps {
  invitation: InvitationCreated | null;
  onClose: () => void;
}

export function InvitationLinkModal({
  invitation,
  onClose,
}: InvitationLinkModalProps): JSX.Element {
  const { t } = useTranslation();
  const toast = useToast();

  const link = invitation
    ? `${window.location.origin}/accept-invitation?token=${encodeURIComponent(invitation.invitation_token)}`
    : '';

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      toast.success(t('admin.users.copiedLink'));
    } catch {
      /* clipboard unavailable */
    }
  };

  return (
    <Modal
      open={invitation !== null}
      onClose={onClose}
      icon="mark_email_read"
      title={t('admin.users.inviteTitle')}
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.close')}
          </Button>
          <Button variant="primary" icon="content_copy" onClick={() => void handleCopy()}>
            {t('admin.users.copyLink')}
          </Button>
        </>
      }
    >
      {invitation && (
        <div className="flex flex-col gap-4">
          <p className="font-body-sm text-body-sm text-on-surface-variant">
            {t('admin.users.inviteSubtitle', { date: formatDate(invitation.expires_at) })}
          </p>
          <Input
            id="invitation-link"
            label={t('admin.users.inviteLinkLabel')}
            value={link}
            readOnly
            onFocus={(event) => event.currentTarget.select()}
          />
          <p className="font-body-sm text-body-sm text-on-surface-variant">{invitation.email}</p>
        </div>
      )}
    </Modal>
  );
}
