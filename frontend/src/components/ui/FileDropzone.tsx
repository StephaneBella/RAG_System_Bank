import { useRef, useState, type DragEvent } from 'react';

import { cn } from '@/lib/utils';

import { Icon } from './Icon';

interface FileDropzoneProps {
  file: File | null;
  onFileChange: (file: File | null) => void;
  dropHint: string;
  formatsHint: string;
  selectedLabel: string;
  accept?: string;
  error?: string;
}

export function FileDropzone({
  file,
  onFileChange,
  dropHint,
  formatsHint,
  selectedLabel,
  accept = '.pdf,.docx',
  error,
}: FileDropzoneProps): JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const handleDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) onFileChange(dropped);
  };

  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={cn(
          'flex w-full flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed p-5 text-center transition-colors',
          dragging
            ? 'border-primary-container bg-surface-container-low'
            : 'border-outline-variant bg-surface-container-low/40 hover:bg-surface-container-low',
          error && 'border-error',
        )}
      >
        <Icon name="upload_file" className="text-[28px] text-outline" />
        <span className="font-body-sm text-body-sm font-semibold text-primary">{dropHint}</span>
        <span className="font-body-sm text-body-sm text-on-surface-variant">{formatsHint}</span>
        {file && (
          <span className="mt-1 inline-flex items-center gap-1.5 rounded-full bg-surface-container px-3 py-1 font-body-sm text-body-sm text-on-surface">
            <Icon name="description" className="text-[16px]" />
            {selectedLabel.replace('{{name}}', file.name)}
          </span>
        )}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
      />
      {error && <span className="font-body-sm text-body-sm text-error">{error}</span>}
    </div>
  );
}
