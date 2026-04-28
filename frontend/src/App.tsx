import {
  Activity,
  Ban,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Coins,
  Film,
  Image,
  Loader2,
  RefreshCw,
  Search,
  Shield,
  Sparkles,
  UserRound,
  Users,
  WandSparkles,
} from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import {
  adjustCredits,
  getStats,
  getTasks,
  getUsers,
  getWorkflows,
  updateUser,
} from "./api";
import type {
  DashboardStats,
  GenerationTask,
  MembershipTier,
  TaskKind,
  TaskStatus,
  User,
  UserStatus,
  Workflow,
} from "./types";

const statusTone: Record<UserStatus | TaskStatus, string> = {
  active: "green",
  limited: "amber",
  banned: "red",
  queued: "neutral",
  running: "blue",
  completed: "green",
  failed: "red",
  cancelled: "neutral",
};

const taskIcons: Record<TaskKind, typeof Image> = {
  "image.generate": Image,
  "image.edit": WandSparkles,
  "video.image_to_video": Film,
  "prompt.expand": Sparkles,
};

const tierLabels: Record<MembershipTier, string> = {
  free: "Free",
  starter: "Starter",
  pro: "Pro",
  studio: "Studio",
};

function App() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [tasks, setTasks] = useState<GenerationTask[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [notice, setNotice] = useState("Demo data appears when the API is unavailable.");

  async function loadData(search = query) {
    setIsLoading(true);
    const [nextStats, nextUsers, nextTasks, nextWorkflows] = await Promise.all([
      getStats(),
      getUsers(search),
      getTasks(),
      getWorkflows(),
    ]);
    setStats(nextStats);
    setUsers(nextUsers);
    setTasks(nextTasks);
    setWorkflows(nextWorkflows);
    setSelectedUserId((current) => current ?? nextUsers[0]?.id ?? null);
    setIsLoading(false);
  }

  useEffect(() => {
    void loadData("");
  }, []);

  const selectedUser = useMemo(
    () => users.find((user) => user.id === selectedUserId) ?? users[0],
    [selectedUserId, users],
  );

  const selectedUserTasks = useMemo(
    () => tasks.filter((task) => task.user_id === selectedUser?.id),
    [selectedUser?.id, tasks],
  );

  async function handleSearch(value: string) {
    setQuery(value);
    await loadData(value);
  }

  async function handleUserStatus(status: UserStatus) {
    if (!selectedUser) return;
    try {
      const updated = await updateUser(selectedUser.id, { status });
      setUsers((current) => current.map((user) => (user.id === updated.id ? updated : user)));
      setNotice(`Updated ${updated.display_name ?? updated.username ?? updated.id}.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Status update failed.");
    }
  }

  async function handleTier(tier: MembershipTier) {
    if (!selectedUser) return;
    try {
      const updated = await updateUser(selectedUser.id, { membership_tier: tier });
      setUsers((current) => current.map((user) => (user.id === updated.id ? updated : user)));
      setNotice(`Membership set to ${tierLabels[tier]}.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Membership update failed.");
    }
  }

  async function handleCredit(amount: number) {
    if (!selectedUser) return;
    try {
      const updated = await adjustCredits(selectedUser.id, amount, "Admin console adjustment");
      setUsers((current) => current.map((user) => (user.id === updated.id ? updated : user)));
      setNotice(`${amount > 0 ? "Added" : "Removed"} ${Math.abs(amount)} credits.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Credit adjustment failed.");
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar" aria-label="Primary">
        <div className="brand">
          <span className="brand-mark">
            <Bot size={22} />
          </span>
          <div>
            <strong>VibeVision</strong>
            <span>Bot ops</span>
          </div>
        </div>

        <nav className="nav-list">
          <a className="nav-item active" href="#users">
            <Users size={18} />
            Users
          </a>
          <a className="nav-item" href="#tasks">
            <Activity size={18} />
            Tasks
          </a>
          <a className="nav-item" href="#workflows">
            <Sparkles size={18} />
            Workflows
          </a>
          <a className="nav-item" href="#credits">
            <Coins size={18} />
            Credits
          </a>
        </nav>

        <div className="service-panel">
          <span className="eyebrow">Service</span>
          <div className="service-row">
            <span>Ollama</span>
            <StatusDot tone="green" />
          </div>
          <div className="service-row">
            <span>ComfyUI</span>
            <StatusDot tone="amber" />
          </div>
          <div className="service-row">
            <span>Telegram</span>
            <StatusDot tone="green" />
          </div>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <span className="eyebrow">Admin workspace</span>
            <h1>User management</h1>
          </div>
          <div className="toolbar">
            <label className="search-box">
              <Search size={17} />
              <input
                value={query}
                onChange={(event) => void handleSearch(event.target.value)}
                placeholder="Search Telegram users"
              />
            </label>
            <button className="icon-button" onClick={() => void loadData()} aria-label="Refresh">
              <RefreshCw size={18} className={isLoading ? "spin" : ""} />
            </button>
          </div>
        </header>

        <section className="metric-grid" aria-label="Dashboard metrics">
          <Metric label="Users" value={stats?.total_users ?? 0} icon={Users} />
          <Metric label="Active" value={stats?.active_users ?? 0} icon={CheckCircle2} />
          <Metric label="In queue" value={(stats?.queued_tasks ?? 0) + (stats?.running_tasks ?? 0)} icon={Activity} />
          <Metric label="Credits spent" value={stats?.credits_spent ?? 0} icon={Coins} />
        </section>

        <section className="content-grid">
          <section id="users" className="data-region">
            <div className="section-heading">
              <div>
                <span className="eyebrow">Accounts</span>
                <h2>Telegram users</h2>
              </div>
              <span className="notice">{notice}</span>
            </div>

            <div className="user-table" role="table" aria-label="Telegram user table">
              <div className="table-row table-head" role="row">
                <span>User</span>
                <span>Plan</span>
                <span>Credits</span>
                <span>Status</span>
              </div>
              {users.map((user) => (
                <button
                  className={`table-row user-row ${selectedUser?.id === user.id ? "selected" : ""}`}
                  key={user.id}
                  onClick={() => setSelectedUserId(user.id)}
                  role="row"
                >
                  <span className="identity-cell">
                    <span className="avatar">
                      <UserRound size={17} />
                    </span>
                    <span>
                      <strong>{user.display_name ?? user.username ?? `User ${user.id}`}</strong>
                      <small>@{user.username ?? user.telegram_id ?? "unknown"}</small>
                    </span>
                  </span>
                  <span>{tierLabels[user.membership_tier]}</span>
                  <span>{formatNumber(user.credit_balance)}</span>
                  <span>
                    <Badge tone={statusTone[user.status]}>{user.status}</Badge>
                  </span>
                </button>
              ))}
            </div>
          </section>

          <aside className="inspector" id="credits">
            {selectedUser ? (
              <>
                <div className="profile-header">
                  <span className="large-avatar">
                    <UserRound size={28} />
                  </span>
                  <div>
                    <span className="eyebrow">Selected user</span>
                    <h2>{selectedUser.display_name ?? selectedUser.username}</h2>
                    <p>@{selectedUser.username ?? selectedUser.telegram_id}</p>
                  </div>
                </div>

                <div className="balance-band">
                  <span>Credit balance</span>
                  <strong>{formatNumber(selectedUser.credit_balance)}</strong>
                  <small>{formatNumber(selectedUser.total_spent_credits)} spent lifetime</small>
                </div>

                <div className="control-group">
                  <span className="control-label">Membership</span>
                  <div className="segmented">
                    {(["free", "starter", "pro", "studio"] as MembershipTier[]).map((tier) => (
                      <button
                        key={tier}
                        className={selectedUser.membership_tier === tier ? "active" : ""}
                        onClick={() => void handleTier(tier)}
                      >
                        {tierLabels[tier]}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="control-group">
                  <span className="control-label">Account status</span>
                  <div className="icon-actions">
                    <ActionButton label="Active" icon={Shield} onClick={() => void handleUserStatus("active")} />
                    <ActionButton label="Limit" icon={CircleAlert} onClick={() => void handleUserStatus("limited")} />
                    <ActionButton label="Ban" icon={Ban} onClick={() => void handleUserStatus("banned")} />
                  </div>
                </div>

                <div className="control-group">
                  <span className="control-label">Credits</span>
                  <div className="credit-actions">
                    {[50, 200, 1000, -50].map((amount) => (
                      <button key={amount} onClick={() => void handleCredit(amount)}>
                        {amount > 0 ? `+${amount}` : amount}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="task-list" id="tasks">
                  <div className="section-heading compact">
                    <span className="eyebrow">Recent jobs</span>
                    <strong>{selectedUserTasks.length}</strong>
                  </div>
                  {selectedUserTasks.length ? (
                    selectedUserTasks.map((task) => <TaskItem task={task} key={task.id} />)
                  ) : (
                    <p className="empty-state">No recent jobs for this account.</p>
                  )}
                </div>
              </>
            ) : (
              <p className="empty-state">Select a user to manage credits and status.</p>
            )}
          </aside>
        </section>

        <section className="workflow-strip" id="workflows">
          <div className="section-heading">
            <div>
              <span className="eyebrow">Routing</span>
              <h2>ComfyUI workflows</h2>
            </div>
          </div>
          <div className="workflow-grid">
            {workflows.map((workflow) => {
              const Icon = taskIcons[workflow.kind];
              return (
                <article className="workflow-item" key={workflow.id}>
                  <Icon size={21} />
                  <div>
                    <strong>{workflow.name}</strong>
                    <span>{workflow.comfy_workflow_key}</span>
                  </div>
                  <Badge tone={workflow.is_active ? "green" : "neutral"}>
                    {workflow.credit_cost} credits
                  </Badge>
                </article>
              );
            })}
          </div>
        </section>
      </section>
    </main>
  );
}

function Metric({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: number;
  icon: typeof Users;
}) {
  return (
    <div className="metric">
      <Icon size={20} />
      <span>{label}</span>
      <strong>{formatNumber(value)}</strong>
    </div>
  );
}

function TaskItem({ task }: { task: GenerationTask }) {
  const Icon = taskIcons[task.kind];
  return (
    <article className="task-item">
      <Icon size={18} />
      <div>
        <strong>{task.kind}</strong>
        <span>{task.interpreted_prompt ?? task.original_text ?? "No prompt"}</span>
      </div>
      <Badge tone={statusTone[task.status]}>{task.status}</Badge>
    </article>
  );
}

function ActionButton({
  label,
  icon: Icon,
  onClick,
}: {
  label: string;
  icon: typeof Shield;
  onClick: () => void;
}) {
  return (
    <button className="action-button" onClick={onClick}>
      <Icon size={17} />
      <span>{label}</span>
    </button>
  );
}

function Badge({ children, tone }: { children: ReactNode; tone: string }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

function StatusDot({ tone }: { tone: string }) {
  return <span className={`status-dot ${tone}`} />;
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

export default App;
