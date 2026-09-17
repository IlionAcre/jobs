import * as React from "react"
import { cn } from "@/lib/utils"
import { PrimaryButton, SecondaryButton } from "./button"

export interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  isDestructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  isOpen,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  isDestructive = false,
  onConfirm,
  onCancel
}: ConfirmDialogProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in-0 duration-200">
      <div className="bg-background text-foreground border shadow-lg rounded-xl w-full max-w-md p-6 animate-in zoom-in-95 duration-200">
        <h2 className="text-lg font-semibold">{title}</h2>
        <p className="mt-2 text-sm text-muted-foreground">{description}</p>
        <div className="mt-6 flex flex-col-reverse sm:flex-row sm:justify-end gap-3 rounded-b-lg">
          <SecondaryButton onClick={onCancel} className="w-full sm:w-auto">
            {cancelLabel}
          </SecondaryButton>
          <PrimaryButton 
            onClick={onConfirm} 
            variant={isDestructive ? "danger" : "primary"}
            className="w-full sm:w-auto"
          >
            {confirmLabel}
          </PrimaryButton>
        </div>
      </div>
    </div>
  )
}
