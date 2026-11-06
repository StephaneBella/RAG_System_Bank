import { useCallback, useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { getApiErrorMessage } from '@/api/errors';
import { RagApiError, streamChat } from '@/api/rag.api';
import type { ChatSource } from '@/api/types';
import { ChatMarkdown } from '@/components/chat/ChatMarkdown';
import { AppShell } from '@/components/layout/AppShell';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Icon } from '@/components/ui/Icon';
import { useAuth } from '@/context/AuthContext';
import { cn, initials } from '@/lib/utils';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: ChatSource[];
  error?: string;
  retryQuestion?: string;
  isStreaming?: boolean;
}

let messageCounter = 0;

function nextMessageId(): string {
  messageCounter += 1;
  return `message-${Date.now()}-${messageCounter}`;
}

function sourceLabel(source: string): string {
  const base = source.split(/[\\/]/).pop() ?? source;
  const parts = base.split('__');
  const name = parts.length === 3 ? parts[2] : base;
  return name.replace(/\.(pdf|docx)$/i, '');
}

function sourcePage(page: ChatSource['page']): string | null {
  if (page === null || page === undefined || page === '') return null;
  return String(page);
}

function StreamError({
  message,
  onRetry,
}: {
  message: ChatMessage;
  onRetry: (message: ChatMessage) => void;
}): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-classification-secret-border bg-classification-secret-bg px-3.5 py-3 text-classification-secret">
      <div className="flex items-start gap-2">
        <Icon name="error" className="mt-0.5 text-[18px]" />
        <div className="flex flex-col gap-0.5">
          <span className="font-label-md text-label-md font-semibold">{t('chat.errorTitle')}</span>
          <span className="font-body-sm text-body-sm">{message.error}</span>
        </div>
      </div>
      {message.retryQuestion && (
        <div className="pl-7">
          <button
            type="button"
            onClick={() => onRetry(message)}
            className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 font-label-md text-label-md font-semibold text-classification-secret transition-colors hover:bg-classification-secret/10"
          >
            <Icon name="refresh" className="text-[16px]" />
            {t('chat.retry')}
          </button>
        </div>
      )}
    </div>
  );
}

function Sources({ sources }: { sources: ChatSource[] }): JSX.Element | null {
  const { t } = useTranslation();
  if (sources.length === 0) return null;

  const visible = sources.slice(0, 6);
  const hiddenCount = sources.length - visible.length;

  return (
    <div className="mt-3 rounded-xl border border-hairline bg-surface-container-lowest px-3 py-2.5">
      <div className="flex items-center gap-1.5 font-label-md text-label-md uppercase tracking-wide text-on-surface-variant">
        <Icon name="menu_book" className="text-[16px]" />
        {t('chat.sources')}
      </div>
      <ul className="mt-2 flex flex-col gap-1.5">
        {visible.map((source) => {
          const page = sourcePage(source.page);
          const label = sourceLabel(source.source);
          return (
            <li key={`${source.id}-${source.source}-${String(source.page)}`}>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span
                  className="flex-1 min-w-0 truncate font-body-sm text-body-sm font-medium text-on-surface"
                  title={source.source}
                >
                  <a
                    href={`#`}
                    className="hover:underline text-primary transition-colors"
                    onClick={(e) => {
                      e.preventDefault();
                      window.alert(t('chat.opensSource', { source: label }));
                    }}
                  >
                    {label}
                  </a>
                </span>
                {page && (
                  <span className="rounded bg-surface-container px-1.5 py-0.5 font-body-sm text-body-sm text-on-surface-variant">
                    {t('chat.page', { page })}
                  </span>
                )}
                {source.doc_type && (
                  <span className="rounded bg-surface-container px-1.5 py-0.5 font-body-sm text-body-sm text-on-surface-variant">
                    {source.doc_type}
                  </span>
                )}
                {source.business_layer && (
                  <span className="rounded bg-surface-container px-1.5 py-0.5 font-body-sm text-body-sm text-on-surface-variant">
                    {source.business_layer}
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {hiddenCount > 0 && (
        <p className="mt-1.5 font-body-sm text-body-sm text-on-surface-variant">
          {t('chat.moreSources', { count: hiddenCount })}
        </p>
      )}
    </div>
  );
}

export function ChatPage(): JSX.Element {
  const { t } = useTranslation();
  const { user } = useAuth();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);

  const sessionIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const isStreamingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [messages]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const runQuestion = useCallback(
    async (question: string, assistantId: string) => {
      const controller = new AbortController();
      abortRef.current = controller;
      isStreamingRef.current = true;
      setIsStreaming(true);

      let failure: string | undefined;

      try {
        for await (const event of streamChat(
          { question, session_id: sessionIdRef.current ?? undefined },
          controller.signal,
        )) {
          if (event.type === 'session') {
            sessionIdRef.current = event.session_id;
          } else if (event.type === 'token') {
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId
                  ? { ...message, content: message.content + event.text }
                  : message,
              ),
            );
          } else if (event.type === 'sources') {
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId ? { ...message, sources: event.sources } : message,
              ),
            );
          } else if (event.type === 'error') {
            failure = event.message;
          }
        }
      } catch (error) {
        if ((error as Error).name !== 'AbortError') {
          if (error instanceof RagApiError) {
            failure = error.status === 403 ? t('chat.accessDenied') : error.message;
          } else if (error instanceof TypeError) {
            failure = t('errors.network');
          } else {
            failure = getApiErrorMessage(error) ?? t('errors.generic');
          }
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        isStreamingRef.current = false;
        setIsStreaming(false);
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId
              ? {
                  ...message,
                  isStreaming: false,
                  error: failure,
                  retryQuestion: failure ? question : undefined,
                }
              : message,
          ),
        );
      }
    },
    [t],
  );

  const send = useCallback(
    (rawQuestion: string) => {
      const question = rawQuestion.trim();
      if (!question || isStreamingRef.current) return;

      const assistantId = nextMessageId();
      setMessages((current) => [
        ...current,
        { id: nextMessageId(), role: 'user', content: question },
        { id: assistantId, role: 'assistant', content: '', isStreaming: true },
      ]);
      setInput('');
      if (textareaRef.current) textareaRef.current.style.height = 'auto';
      void runQuestion(question, assistantId);
    },
    [runQuestion],
  );

  const retry = useCallback(
    (message: ChatMessage) => {
      if (!message.retryQuestion || isStreamingRef.current) return;
      const assistantId = nextMessageId();
      setMessages((current) => [
        ...current.filter((item) => item.id !== message.id),
        { id: assistantId, role: 'assistant', content: '', isStreaming: true },
      ]);
      void runQuestion(message.retryQuestion, assistantId);
    },
    [runQuestion],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const startNewConversation = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    isStreamingRef.current = false;
    sessionIdRef.current = null;
    setIsStreaming(false);
    setMessages([]);
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.focus();
    }
  }, []);

  const handleInputChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    setInput(event.target.value);
    const element = event.target;
    element.style.height = 'auto';
    element.style.height = `${Math.min(element.scrollHeight, 160)}px`;
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send(input);
    }
  };

  const starters = [
    t('chat.starter1'),
    t('chat.starter2'),
    t('chat.starter3'),
    t('chat.starter4'),
  ];

  return (
    <AppShell>
      <div className="mx-auto flex w-full max-w-4xl flex-col px-6 py-6">
        <div className="flex flex-col gap-3 pb-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="flex items-center gap-2 font-headline-lg text-headline-lg font-semibold text-on-surface">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-surface-container text-primary">
                <Icon name="smart_toy" className="text-[20px]" filled />
              </span>
              {t('chat.title')}
            </h1>
            <p className="mt-1 font-body-md text-body-md text-on-surface-variant">
              {t('chat.subtitle')}
            </p>
          </div>
          <Button
            variant="subtle"
            icon="add"
            onClick={startNewConversation}
            disabled={messages.length === 0 && !isStreaming}
          >
            {t('chat.newChat')}
          </Button>
        </div>

        <Card className="flex h-[calc(100dvh-16rem)] min-h-[30rem] flex-col overflow-hidden">
          <div
            className="flex-1 overflow-y-auto px-4 py-5 sm:px-5"
            role="log"
            aria-live="polite"
            aria-label={t('chat.conversation')}
          >
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center px-2 text-center">
                <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-surface-container text-primary">
                  <Icon name="forum" className="text-[28px]" filled />
                </span>
                <h2 className="mt-4 font-headline-md text-headline-md font-semibold text-on-surface">
                  {t('chat.emptyTitle')}
                </h2>
                <p className="mt-1 max-w-md font-body-md text-body-md text-on-surface-variant">
                  {t('chat.emptyHint')}
                </p>
                <div className="mt-5 grid w-full max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
                  {starters.map((starter) => (
                    <button
                      key={starter}
                      type="button"
                      onClick={() => send(starter)}
                      className="rounded-xl border border-hairline bg-surface-container-lowest px-3.5 py-3 text-left font-body-sm text-body-sm text-on-surface transition-colors hover:border-primary-container/40 hover:bg-surface-container-low"
                    >
                      {starter}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <ul className="flex flex-col gap-5">
                {messages.map((message) => {
                  const isUser = message.role === 'user';
                  return (
                    <li
                      key={message.id}
                      className={cn('flex gap-2.5', isUser ? 'flex-row-reverse' : 'flex-row')}
                    >
                      <span
                        className={cn(
                          'flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[16px]',
                          isUser
                            ? 'bg-surface-container text-primary'
                            : 'bg-primary-container text-white',
                        )}
                      >
                        {isUser ? (
                          <span className="font-label-md text-label-md font-semibold">
                            {initials(user?.email)}
                          </span>
                        ) : (
                          <Icon name="smart_toy" className="text-[18px]" filled />
                        )}
                      </span>

                      <div
                        className={cn(
                          'flex min-w-0 max-w-[85%] flex-col',
                          isUser ? 'items-end' : 'items-start',
                        )}
                      >
                        {message.error ? (
                          <StreamError message={message} onRetry={retry} />
                        ) : (
                          <div
                            className={cn(
                              'rounded-2xl px-3.5 py-2.5',
                              isUser
                                ? 'rounded-br-sm bg-primary-container text-white'
                                : 'rounded-bl-sm bg-surface-container-low text-on-surface',
                            )}
                            >
                              {message.content ? (
                                <>
                                  <ChatMarkdown content={message.content} />
                                  {message.isStreaming && (
                                    <span className="ml-0.5 inline-block animate-pulse">▍</span>
                                  )}
                                </>
                              ) : (
                              <span className="flex items-center gap-1 py-1" aria-label={t('chat.thinking')}>
                                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-on-surface-variant [animation-delay:-0.2s]" />
                                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-on-surface-variant [animation-delay:-0.1s]" />
                                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-on-surface-variant" />
                              </span>
                            )}
                          </div>
                        )}

                        {!isUser && message.sources && message.sources.length > 0 && (
                          <div className="w-full">
                            <Sources sources={message.sources} />
                          </div>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="border-t border-hairline bg-surface-container-lowest px-3 py-3 sm:px-5">
            <div className="flex items-end gap-2 rounded-xl border border-outline-variant bg-surface-container-lowest px-3 py-2 transition-all focus-within:border-primary-container focus-within:ring-2 focus-within:ring-accent/20">
              <textarea
                ref={textareaRef}
                rows={1}
                value={input}
                onChange={handleInputChange}
                onKeyDown={handleKeyDown}
                placeholder={t('chat.placeholder')}
                aria-label={t('chat.placeholder')}
                maxLength={4000}
                className="max-h-40 min-h-[2.5rem] flex-1 resize-none bg-transparent py-2 font-body-md text-body-md text-on-surface placeholder:text-outline focus:outline-none"
              />
              {isStreaming ? (
                <button
                  type="button"
                  onClick={stop}
                  title={t('chat.stop')}
                  aria-label={t('chat.stop')}
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-error-container text-error transition-colors hover:bg-[#ffcdc8]"
                >
                  <Icon name="stop" className="text-[20px]" filled />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => send(input)}
                  disabled={!input.trim()}
                  title={t('chat.send')}
                  aria-label={t('chat.send')}
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-container text-white transition-colors hover:bg-primary disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Icon name="send" className="text-[20px]" filled />
                </button>
              )}
            </div>
            <p className="mt-2 text-center font-body-sm text-body-sm text-on-surface-variant">
              {t('chat.disclaimer')}
            </p>
          </div>
        </Card>
      </div>
    </AppShell>
  );
}
