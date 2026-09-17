"use client";

import { useState } from "react"
import { useQueries, useToggleQueryStatus, useDeleteQuery, useCreateQuery } from "@/lib/api/hooks"
import { QueryTable } from "@/components/ui/query-table"
import { EmptyState } from "@/components/ui/empty-state"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { PrimaryButton, SecondaryButton } from "@/components/ui/button"
import { useToast } from "@/components/ui/toast"
import { ListOrdered } from "lucide-react"
import { api } from "@/lib/api/client"

export default function QueriesPage() {
  const { data: queries, isLoading } = useQueries();
  const toggleStatus = useToggleQueryStatus();
  const deleteQuery = useDeleteQuery();
  const createQuery = useCreateQuery();
  const { toast } = useToast();

  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [newQueryName, setNewQueryName] = useState("");
  const [newQueryInterval, setNewQueryInterval] = useState("Hourly");

  const [deleteId, setDeleteId] = useState<string | null>(null);

  const handleToggle = (id: string, status: "active" | "paused") => {
    toggleStatus.mutate({ id, currentStatus: status }, {
      onSuccess: () => {
        toast({ title: "Status Updated", description: `Query is now ${status === 'active' ? 'paused' : 'active'}.`, type: "success" });
      }
    });
  };

  const handleDelete = () => {
    if (deleteId) {
      deleteQuery.mutate(deleteId, {
        onSuccess: () => {
          toast({ title: "Query Deleted", description: "The query has been removed.", type: "success" });
          setDeleteId(null);
        }
      });
    }
  };

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newQueryName.trim()) return;
    
    createQuery.mutate({ name: newQueryName, interval: newQueryInterval }, {
      onSuccess: () => {
        toast({ title: "Query Created", description: "New monitoring query is active.", type: "success" });
        setIsCreateOpen(false);
        setNewQueryName("");
      }
    });
  };

  const handleDuplicate = (id: string) => {
    const q = queries?.find(x => x.id === id);
    if (!q) return;
    createQuery.mutate({ name: `${q.name} (Copy)`, interval: q.interval }, {
      onSuccess: () => {
        toast({ title: "Query Duplicated", description: "A copy has been created.", type: "success" });
      }
    });
  };

  const handleTest = async (id: string) => {
    try {
      await api.testQuery(id);
      toast({ title: "Test Successful", description: "A simulated alert was sent to your Telegram.", type: "success" });
    } catch {
      toast({ title: "Test Failed", description: "Could not send test alert.", type: "error" });
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Manage Queries</h1>
          <p className="text-muted-foreground mt-1">Create and configure your job search filters.</p>
        </div>
        <PrimaryButton onClick={() => setIsCreateOpen(true)}>Create Query</PrimaryButton>
      </div>

      {isCreateOpen && (
        <div className="bg-card rounded-xl border p-6 mb-6 shadow-sm animate-in fade-in zoom-in-95 duration-200">
          <h3 className="text-lg font-semibold mb-4">New Query Request</h3>
          <form className="flex flex-col md:flex-row gap-4 items-end" onSubmit={handleCreate}>
            <div className="w-full">
              <label className="text-sm font-medium mb-1.5 block" htmlFor="q_name">Query Name / Keywords</label>
              <input 
                id="q_name"
                value={newQueryName}
                onChange={(e) => setNewQueryName(e.target.value)}
                placeholder="e.g. React Developer Remote" 
                className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
                required 
              />
            </div>
            <div className="flex gap-2 w-full md:w-auto">
              <SecondaryButton type="button" onClick={() => setIsCreateOpen(false)} className="flex-1 md:flex-none">Cancel</SecondaryButton>
              <PrimaryButton type="submit" className="flex-1 md:flex-none">Save & Activate</PrimaryButton>
            </div>
          </form>
        </div>
      )}

      {isLoading ? (
        <div className="h-64 flex items-center justify-center text-muted-foreground">Loading queries...</div>
      ) : !queries || queries.length === 0 ? (
        <EmptyState 
          icon={<ListOrdered />}
          title="No queries yet"
          description="Create your first query to start receiving Telegram alerts for matching jobs."
          primaryAction={{ label: "Create Query", onClick: () => setIsCreateOpen(true) }}
        />
      ) : (
        <QueryTable 
          queries={queries}
          onToggleStatus={handleToggle}
          onDelete={(id) => setDeleteId(id)}
          onEdit={() => toast({ title: "Coming soon", description: "Edit functionality mockup." })}
          onDuplicate={handleDuplicate}
          onTest={handleTest}
        />
      )}

      <ConfirmDialog 
        isOpen={!!deleteId}
        title="Delete Query"
        description="Are you sure you want to delete this query? This action cannot be undone."
        confirmLabel="Delete"
        isDestructive
        onCancel={() => setDeleteId(null)}
        onConfirm={handleDelete}
      />
    </div>
  )
}
