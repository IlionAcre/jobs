import * as React from "react"
import { cn } from "@/lib/utils"
import { PrimaryButton, SecondaryButton } from "./button"

export interface EmptyStateProps extends React.HTMLAttributes<HTMLDivElement> {
  icon?: React.ReactNode;
  title: string;
  description: string;
  primaryAction?: {
    label: string;
    onClick: () => void;
  };
  secondaryAction?: {
    label: string;
    onClick: () => void;
  };
}

export function EmptyState({ 
  icon, 
  title, 
  description, 
  primaryAction, 
  secondaryAction,
  className,
  ...props 
}: EmptyStateProps) {
  return (
    <div 
      className={cn("flex flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center animate-in fade-in-50", className)}
      {...props}
    >
      {icon && (
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-muted">
          {icon}
        </div>
      )}
      <h3 className="mt-4 text-lg font-semibold">{title}</h3>
      <p className="mb-4 mt-2 text-sm text-muted-foreground max-w-sm">
        {description}
      </p>
      
      <div className="flex flex-col sm:flex-row gap-3 mt-2">
        {primaryAction && (
          <PrimaryButton onClick={primaryAction.onClick}>
            {primaryAction.label}
          </PrimaryButton>
        )}
        {secondaryAction && (
          <SecondaryButton onClick={secondaryAction.onClick}>
            {secondaryAction.label}
          </SecondaryButton>
        )}
      </div>
    </div>
  )
}
