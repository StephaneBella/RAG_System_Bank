import { Spinner } from '@/components/ui/Spinner';

export function FullPageLoader(): JSX.Element {
  return (
    <div className="flex h-screen w-full flex-col items-center justify-center gap-3 text-primary">
      <Spinner className="h-6 w-6" />
      <span className="font-code-mono text-code-mono uppercase tracking-wider">
        Chargement sécurisé…
      </span>
    </div>
  );
}
