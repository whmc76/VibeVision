import type {
  DashboardStats,
  GenerationTask,
  MembershipTier,
  ServiceActionResponse,
  ServiceOverview,
  User,
  UserStatus,
  Workflow,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:18751";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/api${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    ...init,
  });

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(
      `Request failed (${response.status}${response.statusText ? ` ${response.statusText}` : ""}): ${detail}`,
    );
  }

  return response.json() as Promise<T>;
}

async function readErrorDetail(response: Response): Promise<string> {
  const raw = (await response.text()).trim();
  if (!raw) {
    return "No error detail returned by the server.";
  }

  try {
    const payload = JSON.parse(raw) as Record<string, unknown>;
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
    if (typeof payload.message === "string" && payload.message.trim()) {
      return payload.message;
    }
    return JSON.stringify(payload);
  } catch {
    return raw;
  }
}

export async function getStats(): Promise<DashboardStats> {
  return request<DashboardStats>("/admin/stats");
}

export async function getUsers(query = ""): Promise<User[]> {
  const suffix = query ? `?query=${encodeURIComponent(query)}` : "";
  return request<User[]>(`/admin/users${suffix}`);
}

export async function getTasks(): Promise<GenerationTask[]> {
  return request<GenerationTask[]>("/admin/tasks");
}

export async function getWorkflows(): Promise<Workflow[]> {
  return request<Workflow[]>("/admin/workflows");
}

export async function getServices(): Promise<ServiceOverview> {
  return request<ServiceOverview>("/admin/services");
}

export async function serviceAction(
  service: string,
  action: "start" | "stop" | "restart",
): Promise<ServiceActionResponse> {
  return request<ServiceActionResponse>(`/admin/services/${service}/${action}`, {
    method: "POST",
  });
}

export async function updateUser(
  userId: number,
  payload: { status?: UserStatus; membership_tier?: MembershipTier; display_name?: string },
): Promise<User> {
  return request<User>(`/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function adjustCredits(
  userId: number,
  amount: number,
  note: string,
): Promise<User> {
  return request<User>(`/admin/users/${userId}/credits`, {
    method: "POST",
    body: JSON.stringify({ amount, note }),
  });
}

export async function rechargeUser(userId: number, plan: "monthly" | "premium"): Promise<User> {
  return request<User>(`/admin/users/${userId}/recharge`, {
    method: "POST",
    body: JSON.stringify({ plan }),
  });
}
