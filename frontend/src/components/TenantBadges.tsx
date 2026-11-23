import { useAuthMe } from "../hooks/useAuthMe";

import { planLabels } from "../utils/tenant";

const planColors = {
  free: "bg-slate-100 text-slate-700",
  standard: "bg-blue-100 text-blue-800",
  pro: "bg-purple-100 text-purple-800",
};

export function TenantBadges({ showPlan = false }: { showPlan?: boolean }) {
  const auth = useAuthMe();
  if (!auth.data || auth.isError) return null;
  return (
    <div className="flex flex-wrap items-center gap-2" aria-label="当前租户">
      <span className="tenant-badge">{auth.data.tenant_name}</span>
      {showPlan && <span className={`rounded-md px-2.5 py-1 text-sm font-medium ${planColors[auth.data.plan]}`}>{planLabels[auth.data.plan]}套餐</span>}
      <span className="tenant-badge font-mono">{auth.data.tenant_id}</span>
      {showPlan && <span className="text-sm text-muted">今日检索 {auth.data.usage.retrieve_daily_count.toLocaleString()} / {auth.data.limits.retrieve_daily.toLocaleString()}</span>}
    </div>
  );
}
