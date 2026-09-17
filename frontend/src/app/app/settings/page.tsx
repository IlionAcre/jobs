"use client";

import { useUser } from "@/lib/api/hooks"
import { PrimaryButton, SecondaryButton } from "@/components/ui/button"
import { useToast } from "@/components/ui/toast"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { useState } from "react"
import Link from "next/link"

export default function SettingsPage() {
  const { data: user, isLoading } = useUser();
  const { toast } = useToast();
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    toast({ title: "Settings Saved", description: "Your profile has been updated.", type: "success" });
  };

  if (isLoading) return <div className="animate-pulse h-64 bg-card rounded-xl border" />

  return (
    <div className="space-y-8 animate-in fade-in duration-500 max-w-2xl">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Account Settings</h1>
        <p className="text-muted-foreground mt-1">Manage your profile and preferences.</p>
      </div>

      <div className="bg-card rounded-xl border shadow-sm p-6">
        <form onSubmit={handleSave} className="space-y-6">
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium mb-1.5 block">Full Name</label>
              <input 
                defaultValue={user?.name}
                className="w-full h-10 rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </div>
            <div>
              <label className="text-sm font-medium mb-1.5 block">Email Address</label>
              <input 
                type="email"
                defaultValue={user?.email}
                disabled
                className="w-full h-10 rounded-md border border-input bg-muted px-3 py-2 text-sm text-muted-foreground cursor-not-allowed"
              />
              <p className="text-xs text-muted-foreground mt-1">Email cannot be changed currently.</p>
            </div>
          </div>
          
          <PrimaryButton type="submit">Save Changes</PrimaryButton>
        </form>
      </div>

      <div className="bg-card rounded-xl border border-destructive/20 shadow-sm p-6">
        <h3 className="text-lg font-semibold text-destructive mb-2">Danger Zone</h3>
        <p className="text-sm text-muted-foreground mb-4">
          Permanently delete your account and all associated data. This action cannot be reversed.
        </p>
        <SecondaryButton 
          variant="danger" 
          onClick={() => setIsDeleteOpen(true)}
        >
          Delete Account
        </SecondaryButton>
      </div>

      <div className="flex justify-start">
        <Link href="/bot" target="_blank">
          <SecondaryButton>Open Bot</SecondaryButton>
        </Link>
      </div>

      <ConfirmDialog
        isOpen={isDeleteOpen}
        title="Delete Account"
        description="Are you absolutely sure? This will permanently delete your account, active queries, and remove your data from our servers."
        confirmLabel="Yes, delete account"
        isDestructive
        onCancel={() => setIsDeleteOpen(false)}
        onConfirm={() => {
          setIsDeleteOpen(false);
          toast({ title: "Account Deleted", description: "Your account has been permanently removed.", type: "error" });
        }}
      />
    </div>
  )
}
