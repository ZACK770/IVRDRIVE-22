/**
 * The console talks to the backend service across origins, so the base URL is
 * a build-time variable and every call carries the operator token.
 */
const BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

export const tokenStore = {
  get: () => localStorage.getItem("drivers.token") ?? "",
  set: (value: string) => localStorage.setItem("drivers.token", value),
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      "x-admin-token": tokenStore.get(),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

export type OrderStatus = "new" | "assigned" | "on_route" | "done" | "cancelled";

export interface Order {
  id: number;
  created_at: string;
  phone: string;
  origin: string;
  destination: string;
  passengers: number;
  pickup_time: string | null;
  price: number | null;
  notes: string | null;
  status: OrderStatus;
  driver_name: string | null;
  driver_phone: string | null;
}

export interface Call {
  id: number;
  call_id: string;
  phone: string | null;
  started_at: string;
  summary: string | null;
  cost_usd: number;
}

export interface CallDetail extends Omit<Call, "cost_usd"> {
  transcript: string | null;
  stats: {
    turns?: number;
    interruptions?: number;
    reply_latency_ms?: number[];
    tool_calls?: { name: string; result: unknown }[];
    usage?: {
      cost_usd?: number;
      input_tokens?: Record<string, number>;
      output_tokens?: Record<string, number>;
    };
  };
}

export interface Summary {
  orders_24h: number;
  calls_24h: number;
  cost_usd_24h: number;
  by_status: Record<OrderStatus, number>;
}

export interface Price {
  id: number;
  origin: string;
  destination: string;
  price: number;
}

export interface Customer {
  id: number;
  phone: string;
  name: string | null;
  default_pickup: string | null;
  notes: string | null;
}

export const api = {
  summary: () => request<Summary>("/api/summary"),
  orders: () => request<{ orders: Order[] }>("/api/orders").then((r) => r.orders),
  updateOrder: (id: number, patch: Partial<Order>) =>
    request<Order>(`/api/orders/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  calls: () => request<{ calls: Call[] }>("/api/calls").then((r) => r.calls),
  call: (id: number) => request<CallDetail>(`/api/calls/${id}`),
  prices: () => request<{ prices: Price[] }>("/api/prices").then((r) => r.prices),
  customers: () =>
    request<{ customers: Customer[] }>("/api/customers").then((r) => r.customers),
  prompt: () => request<{ content: string }>("/api/prompt").then((r) => r.content),
  savePrompt: (content: string) =>
    request<{ content: string }>("/api/prompt", {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
};
