export interface User {
  id: string;
  email: string;
  name: string;
  avatar_url?: string;
}

export interface AuthProvider {
  id: string;
  name: string;
}

export interface Subscription {
  status: "active" | "inactive" | "trial" | "past_due";
  plan_name: string;
  current_period_end: string;
  cancel_at_period_end: boolean;
}

export interface Query {
  id: string;
  name: string;
  status: "active" | "paused";
  interval: string;
  lastRun: string;
}

export interface CreateQueryPayload {
  name: string;
  interval: string;
}

export interface DashboardSummary {
  active_queries: number;
  total_alerts_this_month: number;
  subscription_status: Subscription["status"];
}
