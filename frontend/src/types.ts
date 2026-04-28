export type UserStatus = "active" | "limited" | "banned";
export type MembershipTier = "free" | "starter" | "pro" | "studio";
export type TaskKind =
  | "image.generate"
  | "image.edit"
  | "video.image_to_video"
  | "prompt.expand";
export type TaskStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface User {
  id: number;
  telegram_id: string | null;
  username: string | null;
  display_name: string | null;
  status: UserStatus;
  membership_tier: MembershipTier;
  credit_balance: number;
  total_spent_credits: number;
  created_at: string;
  updated_at: string;
}

export interface GenerationTask {
  id: number;
  user_id: number;
  workflow_id: number | null;
  kind: TaskKind;
  status: TaskStatus;
  original_text: string | null;
  interpreted_prompt: string | null;
  source_media_url: string | null;
  result_urls: string[];
  credit_cost: number;
  error_message: string | null;
  external_job_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Workflow {
  id: number;
  name: string;
  kind: TaskKind;
  comfy_workflow_key: string;
  credit_cost: number;
  is_active: boolean;
  description: string | null;
  created_at: string;
}

export interface DashboardStats {
  total_users: number;
  active_users: number;
  queued_tasks: number;
  running_tasks: number;
  completed_tasks: number;
  credits_spent: number;
}
