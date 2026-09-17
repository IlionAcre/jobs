import { User, AuthProvider, Subscription, Query, CreateQueryPayload, DashboardSummary } from "./types";

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// Mock Data Storage using sessionStorage if in browser, otherwise memory
let mockUser: User | null = {
  id: "usr_123",
  email: "demo@alerta.com",
  name: "Demo User",
};

let mockSubscription: Subscription = {
  status: "trial",
  plan_name: "Pro Plan",
  current_period_end: new Date(Date.now() + 14 * 24 * 60 * 60 * 1000).toISOString(),
  cancel_at_period_end: false,
};

let mockQueries: Query[] = [
  { id: "q_1", name: "Frontend Engineer - NY", status: "active", interval: "Hourly", lastRun: "10 mins ago" },
  { id: "q_2", name: "React Developer - Remote", status: "paused", interval: "Daily", lastRun: "Yesterday" }
];

// Stubs
export const api = {
  // Auth
  getMe: async (): Promise<User | null> => {
    await delay(300);
    // return null to simulate logged out, returning mockUser to simulate logged in
    const isLoggedOut = typeof window !== 'undefined' && localStorage.getItem("alerta_logged_out") === "true";
    if (isLoggedOut) return null;
    return mockUser;
  },
  
  logout: async (): Promise<void> => {
    await delay(200);
    if (typeof window !== 'undefined') localStorage.setItem("alerta_logged_out", "true");
  },
  
  loginGoogleStart: async (): Promise<string> => {
    await delay(200);
    if (typeof window !== 'undefined') localStorage.removeItem("alerta_logged_out");
    return "/app"; // mock redirect path
  },

  // Subscriptions
  getSubscription: async (): Promise<Subscription> => {
    await delay(400);
    return mockSubscription;
  },

  // Dashboard
  getDashboardSummary: async (): Promise<DashboardSummary> => {
    await delay(500);
    return {
      active_queries: mockQueries.filter(q => q.status === "active").length,
      total_alerts_this_month: 243,
      subscription_status: mockSubscription.status,
    };
  },

  // Queries
  getQueries: async (): Promise<Query[]> => {
    await delay(600);
    return [...mockQueries];
  },

  createQuery: async (payload: CreateQueryPayload): Promise<Query> => {
    await delay(400);
    const newQuery: Query = {
      id: `q_${Date.now()}`,
      name: payload.name,
      interval: payload.interval,
      status: "active",
      lastRun: "Never",
    };
    mockQueries.push(newQuery);
    return newQuery;
  },

  updateQuery: async (id: string, updates: Partial<Query>): Promise<Query> => {
    await delay(300);
    const index = mockQueries.findIndex(q => q.id === id);
    if (index === -1) throw new Error("Not found");
    mockQueries[index] = { ...mockQueries[index], ...updates };
    return mockQueries[index];
  },

  deleteQuery: async (id: string): Promise<void> => {
    await delay(300);
    mockQueries = mockQueries.filter(q => q.id !== id);
  },

  pauseQuery: async (id: string): Promise<Query> => api.updateQuery(id, { status: "paused" }),
  resumeQuery: async (id: string): Promise<Query> => api.updateQuery(id, { status: "active" }),
  
  testQuery: async (id: string): Promise<{ success: boolean; message: string }> => {
    await delay(800);
    return { success: true, message: "Test alert sent to Telegram" };
  },
};
