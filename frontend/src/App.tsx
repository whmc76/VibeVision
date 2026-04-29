import {
  Activity,
  Ban,
  Bot,
  CheckCircle2,
  CircleAlert,
  Coins,
  CreditCard,
  Cpu,
  Film,
  Image,
  LoaderCircle,
  Play,
  RefreshCw,
  Search,
  Server,
  Shield,
  Sparkles,
  Square,
  UserRound,
  Users,
  WandSparkles,
} from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react";

import {
  adjustCredits,
  getServices,
  getStats,
  getTasks,
  getUsers,
  getWorkflows,
  rechargeUser,
  serviceAction,
  updateUser,
} from "./api";
import type {
  DashboardStats,
  GenerationTask,
  MembershipTier,
  ServiceOverview,
  ServiceStatus,
  TaskKind,
  TaskStatus,
  User,
  UserStatus,
  Workflow,
} from "./types";

type ServiceAction = "start" | "stop" | "restart";
const LIVE_REFRESH_INTERVAL_MS = 5000;

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

const serviceTone: Record<string, string> = {
  online: "green",
  configured: "green",
  offline: "red",
  unconfigured: "amber",
};

const serviceIcons: Record<string, typeof Server> = {
  api: Server,
  frontend: Activity,
  comfyui: Cpu,
  llm: Sparkles,
  ollama: Sparkles,
  telegram: Bot,
};

const taskIcons: Record<TaskKind, typeof Image> = {
  "image.generate": Image,
  "image.edit": WandSparkles,
  "video.text_to_video": Film,
  "video.image_to_video": Film,
  "prompt.expand": Sparkles,
};

const tierLabels: Record<MembershipTier, string> = {
  free: "游客",
  starter: "正式会员",
  pro: "VIP",
  studio: "SVIP",
};

const pricingPlans = [
  {
    tier: "free",
    name: "游客",
    price: "$0",
    credits: 5,
    detail: "默认赠送 5 积分",
  },
  {
    tier: "starter",
    name: "月度订阅",
    price: "$9.9/月",
    credits: 100,
    detail: "100 积分 + 每日 10 赠送积分",
  },
  {
    tier: "pro",
    name: "高级订阅",
    price: "$29.9/月",
    credits: 330,
    detail: "330 积分 + 每日 30 赠送积分",
    featured: true,
  },
  {
    tier: "studio",
    name: "VIP / SVIP",
    price: "$100+",
    credits: 0,
    detail: "累计 $100 享 1.1 倍，累计 $500 享 1.2 倍",
  },
] satisfies Array<{
  tier: MembershipTier;
  name: string;
  price: string;
  credits: number;
  detail: string;
  featured?: boolean;
}>;

const planLabels: Record<string, string> = {
  monthly: "月度订阅",
  premium: "高级订阅",
};

function App() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [tasks, setTasks] = useState<GenerationTask[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [services, setServices] = useState<ServiceOverview | null>(null);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [pendingServiceAction, setPendingServiceAction] = useState<{
    service: string;
    action: ServiceAction;
  } | null>(null);
  const latestRequestIdRef = useRef(0);

  async function loadData(search = query, silent = false) {
    const requestId = latestRequestIdRef.current + 1;
    latestRequestIdRef.current = requestId;
    if (!silent) {
      setIsLoading(true);
    }
    const [statsResult, usersResult, tasksResult, workflowsResult, servicesResult] =
      await Promise.allSettled([
        getStats(),
        getUsers(search),
        getTasks(),
        getWorkflows(),
        getServices(),
      ]);

    if (latestRequestIdRef.current !== requestId) {
      return;
    }

    if (statsResult.status === "fulfilled") {
      setStats(statsResult.value);
    } else {
      setStats(null);
    }

    if (usersResult.status === "fulfilled") {
      setUsers(usersResult.value);
      setSelectedUserId((current) =>
        usersResult.value.some((user) => user.id === current) ? current : usersResult.value[0]?.id ?? null,
      );
    } else {
      setUsers([]);
      setSelectedUserId(null);
    }

    if (tasksResult.status === "fulfilled") {
      setTasks(tasksResult.value);
    } else {
      setTasks([]);
    }

    if (workflowsResult.status === "fulfilled") {
      setWorkflows(workflowsResult.value);
    } else {
      setWorkflows([]);
    }

    if (servicesResult.status === "fulfilled") {
      setServices(servicesResult.value);
    } else {
      setServices(null);
    }

    const firstFailure = [statsResult, usersResult, tasksResult, workflowsResult, servicesResult].find(
      (result) => result.status === "rejected",
    );
    setNotice(
      firstFailure?.status === "rejected"
        ? `Live data unavailable. ${getErrorMessage(firstFailure.reason, "Request failed.")}`
        : "",
    );
    if (!silent) {
      setIsLoading(false);
    }
  }

  const refreshLiveData = useEffectEvent(async () => {
    await loadData(query, true);
  });

  useEffect(() => {
    void loadData("");
  }, []);

  useEffect(() => {
    const interval = window.setInterval(() => {
      void refreshLiveData();
    }, LIVE_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [refreshLiveData]);

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

  async function handleRecharge(plan: "monthly" | "premium") {
    if (!selectedUser) return;
    try {
      const updated = await rechargeUser(selectedUser.id, plan);
      setUsers((current) => current.map((user) => (user.id === updated.id ? updated : user)));
      setNotice(`${planLabels[plan]} recharge applied.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Recharge failed.");
    }
  }

  async function handleServiceAction(service: string, action: ServiceAction) {
    if (pendingServiceAction) return;
    setPendingServiceAction({ service, action });
    setNotice(`${action} requested for ${service}.`);
    try {
      const result = await serviceAction(service, action);
      setNotice(result.message);
      setServices(await getServices());
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Service action failed.");
    } finally {
      setPendingServiceAction(null);
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
          <a className="nav-item" href="#pricing">
            <CreditCard size={18} />
            Pricing
          </a>
          <a className="nav-item" href="#monitor">
            <Server size={18} />
            Monitor
          </a>
          <a className="nav-item" href="#credits">
            <Coins size={18} />
            Credits
          </a>
        </nav>

        <div className="service-panel">
          <span className="eyebrow">Service</span>
          {(services?.services?.length ?? 0) > 0 ? (
            (services?.services ?? []).slice(0, 5).map((service) => (
              <div className="service-row" key={service.key}>
                <span className="service-row-name">
                  <StatusDot tone={serviceTone[service.status] ?? "neutral"} />
                  {service.name}
                </span>
                <span className={`service-row-status ${serviceTone[service.status] ?? "neutral"}`}>
                  {service.status}
                </span>
              </div>
            ))
          ) : (
            <p className="empty-state">No live service data.</p>
          )}
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

        <section className="pricing-strip" id="pricing">
          <div className="section-heading">
            <div>
              <span className="eyebrow">Pricing</span>
              <h2>Credit packages</h2>
            </div>
            <div className="rate-summary">
              <span>图片任务 1 积分</span>
              <span>视频任务 10 积分</span>
              <span>首充 +30 积分</span>
              <span>赠送积分优先消耗</span>
            </div>
          </div>
          <div className="pricing-grid">
            {pricingPlans.map((plan) => (
              <article className={`pricing-item ${plan.featured ? "featured" : ""}`} key={plan.tier}>
                <span>{plan.name}</span>
                <strong>{plan.price}</strong>
                <small>{plan.credits > 0 ? `${plan.credits} 积分` : "充值倍率"}</small>
                <p>{plan.detail}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="monitor-region" id="monitor">
          <div className="section-heading">
            <div>
              <span className="eyebrow">Runtime</span>
              <h2>Service monitor</h2>
            </div>
            <div className="queue-summary">
              <span>Running {services?.queue_running ?? 0}</span>
              <span>Pending {services?.queue_pending ?? 0}</span>
            </div>
          </div>
          {(services?.services?.length ?? 0) > 0 ? (
            <div className="service-grid">
              {(services?.services ?? []).map((service) => (
                <ServiceCard
                  key={service.key}
                  service={service}
                  onAction={handleServiceAction}
                  queueRunning={services?.queue_running ?? 0}
                  queuePending={services?.queue_pending ?? 0}
                  pendingAction={
                    pendingServiceAction?.service === service.key ? pendingServiceAction.action : null
                  }
                />
              ))}
            </div>
          ) : (
            <p className="empty-state">No live service data available.</p>
          )}
        </section>

        <section className="content-grid">
          <section id="users" className="data-region">
            <div className="section-heading">
              <div>
                <span className="eyebrow">Accounts</span>
                <h2>Telegram users</h2>
              </div>
              {notice ? <span className="notice">{notice}</span> : null}
            </div>

            <div className="user-table" role="table" aria-label="Telegram user table">
              <div className="table-row table-head" role="row">
                <span>User</span>
                <span>Plan</span>
                <span>Credits</span>
                <span>Status</span>
              </div>
              {users.length ? (
                users.map((user) => (
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
                    <span>{getUserGroupLabel(user)}</span>
                    <span>{formatNumber(getAvailableCredits(user))}</span>
                    <span>
                      <Badge tone={statusTone[user.status]}>{user.status}</Badge>
                    </span>
                  </button>
                ))
              ) : (
                <p className="empty-state table-empty">No live users found.</p>
              )}
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
                    <h2>{selectedUser.display_name ?? selectedUser.username ?? `User ${selectedUser.id}`}</h2>
                    <p>@{selectedUser.username ?? selectedUser.telegram_id}</p>
                  </div>
                </div>

                <div className="balance-band">
                  <span>Available credits</span>
                  <strong>{formatNumber(getAvailableCredits(selectedUser))}</strong>
                  <small>
                    {formatNumber(selectedUser.credit_balance)} paid +{" "}
                    {formatNumber(selectedUser.daily_bonus_balance)} daily
                  </small>
                </div>

                {selectedUser.is_admin || selectedUser.is_hidden ? (
                  <div className="control-group">
                    <span className="control-label">Role</span>
                    <div className="inline-badges">
                      {selectedUser.is_admin ? <Badge tone="blue">Admin</Badge> : null}
                      {selectedUser.is_hidden ? <Badge tone="neutral">Hidden</Badge> : null}
                    </div>
                  </div>
                ) : null}

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

                <div className="account-facts">
                  <span>
                    <small>Subscription</small>
                    <strong>{planLabels[selectedUser.subscription_plan ?? ""] ?? "None"}</strong>
                  </span>
                  <span>
                    <small>Daily reset</small>
                    <strong>{formatNumber(selectedUser.daily_bonus_allowance)} credits</strong>
                  </span>
                  <span>
                    <small>Recharge total</small>
                    <strong>{formatUsd(selectedUser.total_recharge_usd_cents)}</strong>
                  </span>
                  <span>
                    <small>Spent lifetime</small>
                    <strong>{formatNumber(selectedUser.total_spent_credits)}</strong>
                  </span>
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
                    {[10, 50, 100, -10].map((amount) => (
                      <button key={amount} onClick={() => void handleCredit(amount)}>
                        {amount > 0 ? `+${amount}` : amount}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="control-group">
                  <span className="control-label">Recharge</span>
                  <div className="recharge-actions">
                    <button onClick={() => void handleRecharge("monthly")}>$9.9/月 / 100</button>
                    <button onClick={() => void handleRecharge("premium")}>$29.9/月 / 330</button>
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
            ) : users.length ? (
              <p className="empty-state">Select a user to manage credits and status.</p>
            ) : (
              <p className="empty-state">No live user data available.</p>
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
          {workflows.length ? (
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
          ) : (
            <p className="empty-state">No live workflows available.</p>
          )}
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
    <article className={`task-item ${task.error_message ? "has-error" : ""}`}>
      <Icon size={18} />
      <div className="task-copy">
        <strong>
          {task.kind} · #{task.id}
        </strong>
        <span className="task-summary">{task.interpreted_prompt ?? task.original_text ?? "No prompt"}</span>
        {task.error_message ? <code className="task-error">{task.error_message}</code> : null}
      </div>
      <Badge tone={statusTone[task.status]}>{task.status}</Badge>
    </article>
  );
}

function ServiceCard({
  service,
  onAction,
  queueRunning,
  queuePending,
  pendingAction,
}: {
  service: ServiceStatus;
  onAction: (service: string, action: ServiceAction) => Promise<void>;
  queueRunning: number;
  queuePending: number;
  pendingAction: ServiceAction | null;
}) {
  const Icon = serviceIcons[service.key] ?? Server;
  const tone = serviceTone[service.status] ?? "neutral";
  const isComfyUI = service.key === "comfyui";
  const isBusy = pendingAction !== null;
  const serviceFacts = [
    { label: "URL", value: service.url ?? "Not configured", wide: true },
    { label: "Port", value: service.port?.toString() ?? "-" },
    { label: "PID", value: service.pid?.toString() ?? "-" },
    { label: "Process", value: service.process_name ?? "-" },
    { label: "Latency", value: service.latency_ms === null ? "-" : `${service.latency_ms} ms` },
    ...(isComfyUI
      ? [
          { label: "Running", value: queueRunning.toString() },
          { label: "Pending", value: queuePending.toString() },
        ]
      : []),
  ];

  const actions: Array<{
    action: ServiceAction;
    label: string;
    icon: typeof Play;
    disabled: boolean;
  }> = [
    { action: "start", label: "Start", icon: Play, disabled: !service.can_start },
    { action: "restart", label: "Restart", icon: RefreshCw, disabled: !service.can_start && !service.can_stop },
    { action: "stop", label: "Stop", icon: Square, disabled: !service.can_stop },
  ];

  return (
    <article className="service-card">
      <div className="service-card-head">
        <span className="service-icon">
          <Icon size={19} />
        </span>
        <div>
          <strong>{service.name}</strong>
          <span>{service.detail ?? "No service detail reported."}</span>
        </div>
        <Badge tone={tone}>{service.status}</Badge>
      </div>
      <div className="service-facts">
        {serviceFacts.map((fact) => (
          <span className={fact.wide ? "wide" : undefined} key={fact.label}>
            <small>{fact.label}</small>
            <strong>{fact.value}</strong>
          </span>
        ))}
      </div>
      {isComfyUI ? (
        <div className="service-actions">
          {actions.map(({ action, label, icon: ActionIcon, disabled }) => {
            const isCurrentAction = pendingAction === action;
            return (
              <button
                disabled={isBusy || disabled}
                key={action}
                onClick={() => void onAction(service.key, action)}
              >
                {isCurrentAction ? (
                  <LoaderCircle size={15} className="spin" />
                ) : (
                  <ActionIcon size={15} />
                )}
                {isCurrentAction ? "Working" : label}
              </button>
            );
          })}
        </div>
      ) : null}
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

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function getUserGroupLabel(user: User) {
  if (user.is_admin && user.is_hidden) {
    return "Hidden Admin";
  }
  if (user.is_admin) {
    return "Admin";
  }
  if (user.is_hidden) {
    return "Hidden User";
  }
  return tierLabels[user.membership_tier];
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

function formatUsd(cents: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(cents / 100);
}

function getAvailableCredits(user: User) {
  return user.credit_balance + user.daily_bonus_balance;
}

export default App;
