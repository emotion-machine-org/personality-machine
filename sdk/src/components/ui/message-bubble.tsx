import type { ReactNode } from "react";

export interface MessageBubbleProps {
  role: "user" | "assistant" | "system";
  timestamp?: string;
  children: ReactNode;
  className?: string;
}

export function MessageBubble({
  role,
  timestamp,
  children,
  className = "",
}: MessageBubbleProps) {
  const isAssistant = role === "assistant";
  const isUser = role === "user";

  const timestampMarkup = timestamp ? (
    <p
      className={`text-xs text-white opacity-50 ${isUser ? "text-right" : ""}`}
    >
      {new Date(timestamp).toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      })}
    </p>
  ) : null;

  if (isAssistant) {
    return (
      <div className={`flex flex-col items-start space-y-2 ${className}`}>
        <div className="w-fit max-w-[85vw] sm:max-w-xs lg:max-w-md xl:max-w-lg bg-[#161616] rounded-lg px-3 py-2 text-white">
          <p className="m-0 text-[18px] leading-relaxed break-words whitespace-pre-wrap">
            {children}
          </p>
        </div>
        {timestampMarkup}
      </div>
    );
  }

  if (isUser) {
    return (
      <div className={`flex flex-col items-end space-y-2 ${className}`}>
        <div className="max-w-[85vw] sm:max-w-xs lg:max-w-md xl:max-w-lg">
          <p className="text-[18px] font-book leading-relaxed text-white text-right break-words whitespace-pre-wrap">
            {children}
          </p>
        </div>
        {timestampMarkup}
      </div>
    );
  }

  return (
    <div
      className={`flex flex-col items-start space-y-2 text-white/70 ${className}`}
    >
      <p className="text-sm leading-relaxed whitespace-pre-wrap">{children}</p>
      {timestampMarkup}
    </div>
  );
}
