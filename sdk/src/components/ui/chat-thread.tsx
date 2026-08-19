import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

export interface ChatThreadProps {
  children: ReactNode;
  className?: string;
  autoScroll?: boolean;
}

export function ChatThread({
  children,
  className = "",
  autoScroll = true,
}: ChatThreadProps) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!autoScroll) return;
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [children, autoScroll]);

  return (
    <div
      className={`chat-thread flex-1 overflow-y-auto overscroll-contain py-6 space-y-6 ${className}`}
      style={{
        scrollbarWidth: "thin",
        scrollbarColor: "rgba(255,255,255,0.2) transparent",
      }}
    >
      {children}
      <div ref={endRef} />
    </div>
  );
}
