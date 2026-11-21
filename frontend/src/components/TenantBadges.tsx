import { useAuthMe } from "../hooks/useAuthMe";

export function TenantBadges() {
  const auth = useAuthMe();
  if (!auth.data || auth.isError) return null;
  return (
    <div className="flex flex-wrap items-center gap-2" aria-label="当前租户">
      <span className="tenant-badge">{auth.data.tenant_name}</span>
      <span className="tenant-badge font-mono">{auth.data.tenant_id}</span>
    </div>
  );
}
