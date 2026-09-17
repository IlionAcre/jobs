import * as React from "react"
import { cn } from "@/lib/utils"

export interface AuthOptionCardProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  icon: React.ReactNode;
  label: string;
  isPrimary?: boolean;
}

export const AuthOptionCard = React.forwardRef<HTMLButtonElement, AuthOptionCardProps>(
  ({ icon, label, isPrimary, className, ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(
          "flex w-full cursor-pointer items-center justify-center gap-3 rounded-md border p-3.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
          isPrimary 
            ? "bg-primary text-primary-foreground hover:bg-primary/90 border-transparent shadow shadow-primary/20" 
            : "bg-background text-foreground hover:bg-accent border-input shadow-sm",
          "min-h-[44px]", // Mobile hit area
          className
        )}
        {...props}
      >
        <span className={cn("h-5 w-5", isPrimary ? "text-primary-foreground" : "text-foreground")}>
          {icon}
        </span>
        {label}
      </button>
    )
  }
)
AuthOptionCard.displayName = "AuthOptionCard"
