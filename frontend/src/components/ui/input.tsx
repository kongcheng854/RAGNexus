import * as React from "react";
import { cn } from "../../lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn("h-11 w-full rounded-lg border border-border bg-white px-3 text-base outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 disabled:bg-[#f1f5f3]", className)} {...props} />
  ),
);
Input.displayName = "Input";
