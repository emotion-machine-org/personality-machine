import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import { describe, expect, it, afterEach } from 'vitest';
import MemoryHighlightPill from '@/components/ui/memory-highlight-pill';
import type { MemoryHighlight } from '@/hooks/useConversationMessages';

describe('MemoryHighlightPill', () => {
  afterEach(() => {
    cleanup();
  });
  const baseHighlight: MemoryHighlight = {
    messageId: 'message-1',
    sender: 'assistant',
    preview: 'Yesterday you mentioned you love hiking in the Alps.',
    importance: 0.91,
    memoryId: 'memory-123',
    state: 'stored',
    updatedAt: Date.now(),
  };

  it('renders stored memory details with management link', () => {
    render(
      <MemoryHighlightPill highlight={baseHighlight} align="left" manageHref="/relationships?tab=memories" />
    );

    expect(screen.getByText(/Stored memory/i)).toBeInTheDocument();
    expect(screen.getByText(/love hiking/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Manage memories/i })).toHaveAttribute(
      'href',
      '/relationships?tab=memories'
    );
  });

  it('renders queued memory without management link', () => {
    const queuedHighlight: MemoryHighlight = {
      ...baseHighlight,
      state: 'queued',
      preview: 'Pending memory capture',
      memoryId: undefined,
    };

    render(<MemoryHighlightPill highlight={queuedHighlight} align="right" />);

    expect(screen.getByText(/Memory queued/i)).toBeInTheDocument();
    expect(screen.getByText(/Pending memory capture/i)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Manage memories/i })).not.toBeInTheDocument();
  });

  it('does not render when state is pending', () => {
    const { container } = render(
      <MemoryHighlightPill
        highlight={{ ...baseHighlight, state: 'pending', memoryId: undefined, preview: 'Working…' }}
        align="left"
      />
    );
    expect(container.firstChild).toBeNull();
  });
});
