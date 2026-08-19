'use client';

import { ReactNode, useEffect, useRef } from 'react';

export interface ChatThreadProps {
  children: ReactNode;
  className?: string;
  autoScroll?: boolean;
}

export function ChatThread({ children, className = '', autoScroll = true }: ChatThreadProps) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!autoScroll) return;
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [children, autoScroll]);

  return (
    <div
      className={`chat-thread flex-1 overflow-y-auto overscroll-contain py-6 space-y-6 scrollbar-auto-hide ${className}`}
    >
      <style jsx>{`
        .scrollbar-auto-hide {
          scrollbar-width: thin;
          scrollbar-color: transparent transparent;
        }
        .scrollbar-auto-hide:hover {
          scrollbar-color: rgba(255,255,255,0.2) transparent;
        }
        .scrollbar-auto-hide::-webkit-scrollbar {
          width: 6px;
        }
        .scrollbar-auto-hide::-webkit-scrollbar-track {
          background: transparent;
        }
        .scrollbar-auto-hide::-webkit-scrollbar-thumb {
          background: transparent;
          border-radius: 3px;
        }
        .scrollbar-auto-hide:hover::-webkit-scrollbar-thumb {
          background: rgba(255,255,255,0.2);
        }
      `}</style>
      {children}
      <div ref={endRef} />
    </div>
  );
}
