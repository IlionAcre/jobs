import * as React from "react"
import { cn } from "@/lib/utils"

export interface StatusChipProps extends React.HTMLAttributes<HTMLDivElement> {
  status: "active" | "paused" | "trial" | "past_due";
}

const statusStyles = {
  active: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
  paused: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300",
  trial: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300",
  past_due: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
}

const statusLabels = {
  active: "Active",
  paused: "Paused",
  trial: "Trial",
  past_due: "Past Due",
}

export function StatusChip({ status, className, ...props }: StatusChipProps) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold select-none",
        statusStyles[status],
        className
      )}
      {...props}
    >
      {statusLabels[status]}
    </div>
  )
}
