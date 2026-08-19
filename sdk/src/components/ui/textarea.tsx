import { forwardRef } from "react";
import { cn } from "@/lib/utils";

interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  minHeight?: number; // Height in pixels
}

const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, minHeight = 64, ...props }, ref) => {
    return (
      <textarea
        ref={ref}
        className={cn(
          "w-full bg-gray-dark text-white font-book text-xs leading-tight rounded-[7px] px-[10px] py-2 resize-none",
          "placeholder:text-white/40",
          "focus:outline-none focus:ring-none focus:ring-white/40 focus:border-transparent",
          "transition-colors",
          className,
        )}
        style={{ minHeight: `${minHeight}px` }}
        {...props}
      />
    );
  },
);

Textarea.displayName = "Textarea";

export { Textarea };
