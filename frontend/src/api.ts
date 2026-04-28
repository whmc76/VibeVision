import { mockServices, mockStats, mockTasks, mockUsers, mockWorkflows } from "./mockData";
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
    const detail = await response.text();
    throw new Error(detail || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function getStats(): Promise<DashboardStats> {
  try {
    return await request<DashboardStats>("/admin/stats");
  } catch {
    return mockStats;
  }
}

export async function getUsers(query = ""): Promise<User[]> {
  try {
    const suffix = query ? `?query=${encodeURIComponent(query)}` : "";
    return await request<User[]>(`/admin/users${suffix}`);
  } catch {
    const lowered = query.toLowerCase();
    return mockUsers.filter((user) =>
      [user.username, user.display_name, user.telegram_id]
        .filter(Boolean)
        .some((value) => value!.toLowerCase().includes(lowered)),
    );
  }
}

export async function getTasks(): Promise<GenerationTask[]> {
  try {
    return await request<GenerationTask[]>("/admin/tasks");
  } catch {
    return mockTasks;
  }
}

export async function getWorkflows(): Promise<Workflow[]> {
  try {
    return await request<Workflow[]>("/admin/workflows");
  } catch {
    return mockWorkflows;
  }
}

export async function getServices(): Promise<ServiceOverview> {
  try {
    return await request<ServiceOverview>("/admin/services");
  } catch {
    return mockServices;
  }
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
