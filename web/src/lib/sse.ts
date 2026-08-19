export type JobEvent = {
  id: string;
  status?: string;
  total_conversations?: number;
  processed_count?: number;
  error_count?: number;
  error?: string;
};

// Subscribe to SSE using fetch streaming (so we can attach Authorization header)
export async function subscribeJobEvents(url: string, token: string, onEvent: (e: JobEvent) => void, signal?: AbortSignal) {
  const resp = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'text/event-stream'
    },
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`SSE error ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      // Parse SSE lines
      const lines = chunk.split('\n');
      for (const line of lines) {
        if (line.startsWith('data:')) {
          const data = line.slice(5).trim();
          try {
            const evt = JSON.parse(data);
            onEvent(evt);
          } catch {}
        }
      }
    }
  }
}


// Generic SSE parser for text streaming
export type StreamMessage = {
  id: string;
  role: 'system' | 'user' | 'assistant' | string;
  content: string;
  created_at: string;
};

export type TextStreamEvent =
  | { type: 'ack'; data: { user_message: StreamMessage }; id?: string }
  | { type: 'status'; data: { stage: 'retrieving' | 'thinking' | 'ruminating' | 'memory_stored'; phase: 'start' | 'end'; meta?: Record<string, unknown> }; id?: string }
  | { type: 'delta'; data: { content: string }; id?: string }
  | { type: 'message'; data: { assistant_message: StreamMessage }; id?: string }
  | { type: 'timings'; data: Record<string, unknown>; id?: string }
  | { type: 'error'; data: { message?: string; detail?: string }; id?: string }
  | { type: 'done'; data: Record<string, unknown>; id?: string };

export type TextStreamBody = Record<string, unknown>;

export async function subscribeTextStream(
  url: string,
  token: string | null,
  body: TextStreamBody,
  onEvent: (e: TextStreamEvent) => void,
  signal?: AbortSignal
) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`SSE error ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  // Per SSE spec: events are separated by blank lines; fields per line: event:, id:, data:
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);

      let evtType: string | null = null;
      let evtId: string | undefined;
      const dataLines: string[] = [];
      const lines = raw.split('\n');
      for (const line of lines) {
        if (!line.trim()) continue;
        if (line.startsWith('event:')) {
          evtType = line.slice(6).trim();
        } else if (line.startsWith('id:')) {
          evtId = line.slice(3).trim();
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trim());
        }
      }

      const dataStr = dataLines.join('\n');
      let parsed: unknown = {};
      try { parsed = dataStr ? JSON.parse(dataStr) : {}; } catch {}

      if (!evtType) {
        // Fallback: treat as a plain data event (assume delta)
        onEvent({ type: 'delta', data: (parsed as { content: string }) });
        continue;
      }

      // Best-effort mapping by event type
      switch (evtType) {
        case 'ack':
          onEvent({ type: 'ack', data: (parsed as { user_message: StreamMessage }), id: evtId });
          break;
        case 'status':
          onEvent({ type: 'status', data: (parsed as { stage: 'retrieving' | 'thinking' | 'ruminating' | 'memory_stored'; phase: 'start' | 'end'; meta?: Record<string, unknown> }), id: evtId });
          break;
        case 'delta':
          onEvent({ type: 'delta', data: (parsed as { content: string }), id: evtId });
          break;
        case 'message':
          onEvent({ type: 'message', data: (parsed as { assistant_message: StreamMessage }), id: evtId });
          break;
        case 'timings':
          onEvent({ type: 'timings', data: (parsed as Record<string, unknown>), id: evtId });
          break;
        case 'error':
          onEvent({ type: 'error', data: (parsed as { message?: string; detail?: string }), id: evtId });
          break;
        case 'done':
          onEvent({ type: 'done', data: (parsed as Record<string, unknown>), id: evtId });
          break;
        default:
          // Unknown type → pass through as delta
          onEvent({ type: 'delta', data: (parsed as { content: string }), id: evtId });
      }
    }
  }
}
