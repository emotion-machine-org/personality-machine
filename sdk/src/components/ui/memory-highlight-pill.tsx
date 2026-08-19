export interface MemoryHighlight {
  messageId: string;
  sender: string;
  preview: string;
  importance: number;
  memoryId?: string;
  state: 'queued' | 'stored' | 'pending';
  updatedAt: number;
}

interface MemoryHighlightPillProps {
  highlight: MemoryHighlight;
  align: 'left' | 'right';
  manageHref?: string;
}

const ALIGN_CLASS = {
  left: 'items-start text-left',
  right: 'items-end text-right',
} as const;

const MemoryHighlightPill: React.FC<MemoryHighlightPillProps> = ({ highlight, align, manageHref }) => {
  if (highlight.state === 'pending') return null;

  const label = highlight.state === 'stored' ? 'Stored memory' : 'Memory queued';
  const alignment = ALIGN_CLASS[align] ?? ALIGN_CLASS.left;

  return (
    <div className={`flex flex-col gap-1 rounded-md bg-white/10 px-3 py-2 text-xs text-white ${alignment}`}>
      <span className="font-semibold tracking-wide uppercase text-white/80">{label}</span>
      <span className="text-sm text-white/90">{highlight.preview}</span>
      {highlight.state === 'stored' && manageHref ? (
        <a className="text-xs text-white/70 underline underline-offset-2 hover:text-white" href={manageHref}>
          Manage memories
        </a>
      ) : null}
    </div>
  );
};

export default MemoryHighlightPill;
