"use client";

import { useSubscription } from "@/lib/api/hooks"
import { PrimaryButton, SecondaryButton } from "@/components/ui/button"
import { StatusChip } from "@/components/ui/status-chip"
import { EmptyState } from "@/components/ui/empty-state"
import { CreditCard, FileText } from "lucide-react"

export default function BillingPage() {
  const { data: sub, isLoading } = useSubscription();

  const handleStripePortal = () => {
    alert("Redirecting to Stripe Customer Portal...");
  };

  if (isLoading) {
    return <div className="animate-pulse h-64 bg-card rounded-xl border" />
  }

  if (!sub || sub.status === "inactive" || sub.status === "past_due") {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Billing & Plans</h1>
          <p className="text-muted-foreground mt-1">Manage your active subscription.</p>
        </div>
        <EmptyState
          icon={<CreditCard />}
          title={sub?.status === "past_due" ? "Payment failed" : "No active plan"}
          description={sub?.status === "past_due" ? "Please update your payment method to continue." : "Your plan is inactive. Subscribe to pro to start receiving alerts."}
          primaryAction={{ label: "Manage Plan", onClick: handleStripePortal }}
        />
      </div>
    )
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Billing Settings</h1>
        <p className="text-muted-foreground mt-1">Manage your subscription and invoices.</p>
      </div>

      <div className="bg-card rounded-xl border shadow-sm p-6 max-w-2xl">
        <div className="flex justify-between items-start mb-6">
          <div>
            <h2 className="text-xl font-bold">{sub.plan_name}</h2>
            <div className="flex items-center gap-2 mt-2">
              <StatusChip status={sub.status} />
              <span className="text-sm text-muted-foreground text-nowrap">
                {sub.cancel_at_period_end ? "Cancels at" : "Renews at"}: {new Date(sub.current_period_end).toLocaleDateString()}
              </span>
            </div>
          </div>
          <div className="text-right flex flex-col items-end gap-2">
            <span className="text-2xl font-black">$29<span className="text-sm text-muted-foreground font-normal">/mo</span></span>
          </div>
        </div>

        <div className="h-px bg-border my-6" />

        <div className="flex flex-col sm:flex-row gap-3">
          <PrimaryButton onClick={handleStripePortal}>Manage Plan</PrimaryButton>
          <SecondaryButton onClick={handleStripePortal}>Update Payment Method</SecondaryButton>
          {!sub.cancel_at_period_end && (
            <SecondaryButton onClick={handleStripePortal} className="text-destructive hover:text-destructive hover:bg-destructive/10">
              Cancel Plan
            </SecondaryButton>
          )}
        </div>
      </div>

      <div className="bg-card rounded-xl border shadow-sm p-6 max-w-2xl">
        <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <FileText className="h-5 w-5 text-muted-foreground" />
          Recent Invoices
        </h3>
        <div className="divide-y text-sm">
          {/* Mock Invoices */}
          <div className="py-3 flex justify-between items-center">
            <div>
              <p className="font-medium">Pro Plan - March 2024</p>
              <p className="text-muted-foreground">Mar 1, 2024</p>
            </div>
            <div className="flex gap-4 items-center">
              <span className="font-medium">$29.00</span>
              <button className="text-primary hover:underline" onClick={handleStripePortal}>View</button>
            </div>
          </div>
          <div className="py-3 flex justify-between items-center">
            <div>
              <p className="font-medium">Pro Plan - February 2024</p>
              <p className="text-muted-foreground">Feb 1, 2024</p>
            </div>
            <div className="flex gap-4 items-center">
              <span className="font-medium">$29.00</span>
              <button className="text-primary hover:underline" onClick={handleStripePortal}>View</button>
            </div>
          </div>
        </div>
        <div className="mt-4 pt-4 border-t">
          <button className="text-sm text-primary hover:underline font-medium" onClick={handleStripePortal}>
            View All Invoices &rarr;
          </button>
        </div>
      </div>
    </div>
  )
}
