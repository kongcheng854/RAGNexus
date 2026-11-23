import type { IndexStatus } from "../types/api";

const states = {
  1: { label: "成功", style: "status-success" },
  2: { label: "失败", style: "status-failed" },
  3: { label: "索引中", style: "status-uploading" },
};

export function StatusBadge({ status }: { status: IndexStatus }) {
  const { label, style } = states[status];
  return <span className={`inline-flex whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium ${style}`}>{label}</span>;
}
