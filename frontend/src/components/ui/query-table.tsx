import * as React from "react"
import { StatusChip } from "./status-chip"
import { IconButton } from "./button"
import { Play, Pause, Edit, Trash2, Copy, PlayCircle } from "lucide-react"

// Assume we have this type available from API soon
export type QueryData = {
  id: string;
  name: string;
  status: "active" | "paused";
  interval: string;
  lastRun: string;
}

interface QueryTableProps {
  queries: QueryData[];
  onToggleStatus: (id: string, currentStatus: "active"|"paused") => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
  onDuplicate: (id: string) => void;
  onTest: (id: string) => void;
}

export function QueryTable({ queries, onToggleStatus, onEdit, onDelete, onDuplicate, onTest }: QueryTableProps) {
  if (queries.length === 0) {
    return null; // Will show EmptyState outside
  }

  return (
    <div className="w-full">
      {/* Desktop Table View */}
      <div className="hidden md:block rounded-md border text-sm overflow-hidden">
        <table className="w-full text-left">
          <thead className="bg-muted text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {queries.map((q) => (
              <tr key={q.id} className="hover:bg-muted/50 transition-colors bg-card">
                <td className="px-4 py-3 font-medium">{q.name}</td>
                <td className="px-4 py-3">
                  <StatusChip status={q.status} />
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="flex justify-end gap-1">
                    <IconButton 
                      onClick={() => onToggleStatus(q.id, q.status)} 
                      title={q.status === "active" ? "Pause" : "Resume"}
                    >
                      {q.status === "active" ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                    </IconButton>
                    <IconButton onClick={() => onTest(q.id)} title="Test Query">
                      <PlayCircle className="h-4 w-4" />
                    </IconButton>
                    <IconButton onClick={() => onEdit(q.id)} title="Edit">
                      <Edit className="h-4 w-4" />
                    </IconButton>
                    <IconButton onClick={() => onDuplicate(q.id)} title="Duplicate">
                      <Copy className="h-4 w-4" />
                    </IconButton>
                    <IconButton onClick={() => onDelete(q.id)} title="Delete" className="text-destructive hover:text-destructive">
                      <Trash2 className="h-4 w-4" />
                    </IconButton>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile Stacked Cards View */}
      <div className="flex flex-col gap-4 md:hidden">
        {queries.map((q) => (
          <div key={q.id} className="rounded-xl border bg-card p-4 shadow-sm flex flex-col gap-3">
            <div className="flex items-start justify-between">
              <div>
                <h4 className="font-semibold">{q.name}</h4>
              </div>
              <StatusChip status={q.status} />
            </div>
            
            <div className="flex items-center gap-2 mt-2 pt-3 border-t">
              <IconButton 
                className="flex-1 bg-secondary hover:bg-secondary/80 border"
                onClick={() => onToggleStatus(q.id, q.status)} 
              >
                {q.status === "active" ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              </IconButton>
              <IconButton 
                className="flex-1 bg-secondary hover:bg-secondary/80 border"
                onClick={() => onTest(q.id)}
              >
                <PlayCircle className="h-4 w-4" />
              </IconButton>
              <IconButton 
                className="flex-1 bg-secondary hover:bg-secondary/80 border"
                onClick={() => onEdit(q.id)}
              >
                <Edit className="h-4 w-4" />
              </IconButton>
              <IconButton 
                className="flex-[0.5] bg-secondary hover:bg-secondary/80 border text-destructive"
                onClick={() => onDelete(q.id)}
              >
                <Trash2 className="h-4 w-4" />
              </IconButton>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
