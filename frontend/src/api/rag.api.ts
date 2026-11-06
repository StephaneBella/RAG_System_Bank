import { API_BASE_URL, apiClient, triggerUnauthorized } from './client';
import { endpoints } from './endpoints';
import { getToken } from './tokenStore';
import type { ChatRequest, ChatResponse, RagStreamEvent } from './types';

export class RagApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'RagApiError';
    this.status = status;
  }
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === 'string') return payload.detail;
    if (Array.isArray(payload.detail) && payload.detail.length > 0) {
      const first = payload.detail[0] as { msg?: string };
      if (first?.msg) return first.msg;
    }
  } catch {
    /* non-JSON error payload */
  }
  return `Request failed (${response.status})`;
}

function parseSseData(block: string): string | null {
  const lines = block
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart());
  return lines.length > 0 ? lines.join('\n') : null;
}

export async function chat(payload: ChatRequest): Promise<ChatResponse> {
  const { data } = await apiClient.post<ChatResponse>(endpoints.rag.chat, payload);
  return data;
}

/**
 * Streams the RAG answer as Server-Sent Events.
 *
 * `fetch` is used instead of axios because browsers cannot consume a streamed
 * response body through the axios XHR adapter.
 */
export async function* streamChat(
  payload: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<RagStreamEvent> {
  const token = getToken();
  let response: Response;

  try {
    response = await fetch(`${API_BASE_URL}${endpoints.rag.chatStream}`, {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if ((error as Error).name === 'AbortError') return;
    throw error;
  }

  if (response.status === 401) {
    triggerUnauthorized();
  }
  if (!response.ok) {
    throw new RagApiError(await readErrorMessage(response), response.status);
  }
  if (!response.body) {
    throw new RagApiError('Streaming is not supported by this browser.', 0);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, '\n');

      let boundary = buffer.indexOf('\n\n');
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const raw = parseSseData(block);
        if (raw) {
          try {
            yield JSON.parse(raw) as RagStreamEvent;
          } catch {
            /* ignore malformed event */
          }
        }
        boundary = buffer.indexOf('\n\n');
      }
    }

    buffer += decoder.decode();
    const tail = parseSseData(buffer.replace(/\r\n/g, '\n'));
    if (tail) {
      try {
        yield JSON.parse(tail) as RagStreamEvent;
      } catch {
        /* ignore malformed event */
      }
    }
  } catch (error) {
    if ((error as Error).name === 'AbortError') return;
    throw error;
  } finally {
    reader.releaseLock();
  }
}
