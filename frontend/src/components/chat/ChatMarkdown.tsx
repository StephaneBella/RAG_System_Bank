import ReactMarkdown, { type Components } from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';

const components: Components = {
  p: ({ children }) => (
    <p className="whitespace-pre-wrap break-words font-body-md text-body-md leading-relaxed">
      {children}
    </p>
  ),
  h1: ({ children }) => <h1 className="my-4 text-2xl font-bold text-primary">{children}</h1>,
  h2: ({ children }) => <h2 className="my-3 text-xl font-bold text-primary">{children}</h2>,
  h3: ({ children }) => (
    <h3 className="my-2 font-headline-md text-headline-md font-bold text-primary">{children}</h3>
  ),
  ul: ({ children }) => <ul className="list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="font-body-md text-body-md">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  code: ({ className, children }) => {
    const isBlock = typeof className === 'string' && className.startsWith('language-');
    if (isBlock) {
      return <code className="font-mono text-code-mono">{children}</code>;
    }
    return (
      <code className="rounded bg-surface-container px-1 py-0.5 font-mono text-code-mono">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-lg bg-surface-container p-3 text-on-surface">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-hairline bg-surface-container-lowest p-2 pl-4 italic">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-4 border-hairline" />,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer noopener" className="text-primary underline">
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse border border-hairline">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-hairline px-2 py-1 font-body-sm text-body-sm font-medium text-on-surface">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-hairline px-2 py-1 font-body-sm text-body-sm text-on-surface">
      {children}
    </td>
  ),
};

export function ChatMarkdown({ content }: { content: string }): JSX.Element {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={components}>
      {content}
    </ReactMarkdown>
  );
}
