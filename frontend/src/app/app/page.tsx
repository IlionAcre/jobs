"use client";

import { useDashboardSummary, useQueries } from "@/lib/api/hooks"
import { MetricCard } from "@/components/ui/metric-card"
import { Activity, Bell, CreditCard } from "lucide-react"
import Link from "next/link"
import { PrimaryButton } from "@/components/ui/button"

export default function DashboardPage() {
  const { data: summary, isLoading: loadingSummary } = useDashboardSummary();
  const { data: queries, isLoading: loadingQueries } = useQueries();

  return (
    <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dashboard Overview</h1>
          <p className="text-muted-foreground mt-1">Here is what is happening with your alerts today.</p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <MetricCard
          title="Active Queries"
          value={loadingSummary ? "-" : summary?.active_queries || 0}
          description="Monitoring queries currently active"
          icon={<Activity className="text-primary" />}
        />
        <MetricCard
          title="Alerts This Month"
          value={loadingSummary ? "-" : summary?.total_alerts_this_month || 0}
          description="Total notifications sent to Telegram"
          icon={<Bell className="text-primary" />}
        />
        <MetricCard
          title="Subscription"
          value={loadingSummary ? "-" : (summary?.subscription_status === "trial" ? "Trial" : "Pro")}
          description={summary?.subscription_status === "trial" ? "14 days remaining" : "Active plan"}
          icon={<CreditCard className="text-primary" />}
        />
      </div>

      <div className="bg-card rounded-xl border shadow-sm p-6 mt-8">
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-lg font-semibold">Recent Queries</h2>
          <Link href="/app/queries" className="text-sm text-primary hover:underline font-medium">
            Manage All
          </Link>
        </div>
        
        {loadingQueries ? (
          <div className="h-32 flex items-center justify-center text-muted-foreground text-sm">
            Loading queries...
          </div>
        ) : !queries || queries.length === 0 ? (
          <div className="h-32 flex flex-col items-center justify-center text-muted-foreground text-sm border border-dashed rounded-lg">
            <p>No queries active yet.</p>
            <Link href="/app/queries" className="text-primary hover:underline mt-1">Create your first query</Link>
          </div>
        ) : (
          <div className="divide-y border rounded-lg overflow-hidden">
            {queries.slice(0, 3).map(q => (
              <div key={q.id} className="p-4 flex justify-between items-center hover:bg-muted/50 transition-colors">
                <div>
                  <h4 className="font-medium text-sm">{q.name}</h4>
                </div>
                <div className={`text-xs px-2 py-1 rounded-full ${q.status === 'active' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800'}`}>
                  {q.status === 'active' ? 'Active' : 'Paused'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
