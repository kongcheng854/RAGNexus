import * as React from "react";
import { cn } from "../../lib/utils";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea ref={ref} className={cn("w-full resize-y rounded-lg border border-border bg-white p-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 disabled:bg-[#f1f5f3]", className)} {...props} />
  ),
);
Textarea.displayName = "Textarea";
