import { Check, CircleAlert, FileText, LoaderCircle, Trash2 } from "lucide-react";
import type { UploadItem } from "../types/api";
import { formatBytes } from "../utils/files";
import { Button } from "./ui/button";

const statusLabels = { pending: "等待上传", uploading: "正在提交", success: "提交成功", failed: "上传失败" };

export function UploadTable({ items, running, remove }: {
  items: UploadItem[]; running: boolean; remove: (id: string) => void;
}) {
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[580px] text-left">
        <thead className="border-y border-border bg-[#fafcfb] text-sm font-normal text-muted">
          <tr><th className="px-6 py-3 font-medium">文件名称</th><th className="px-4 py-3 font-medium">大小</th><th className="px-4 py-3 font-medium">状态 / 处理结果</th><th className="px-4 py-3"><span className="sr-only">移除</span></th></tr>
        </thead>
        <tbody className="divide-y divide-[#edf1ef]">
          {items.map((item) => (
            <tr key={item.id}>
              <td className="max-w-xs px-6 py-4 align-top">
                <div className="flex items-start gap-3"><FileText size={20} className="mt-0.5 shrink-0 text-[#82968c]" /><span className="break-all text-sm font-medium">{item.path}</span></div>
              </td>
              <td className="whitespace-nowrap px-4 py-4 align-top text-sm text-muted">{formatBytes(item.file.size)}</td>
              <td className="max-w-sm px-4 py-4 align-top">
                <div className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-sm status-${item.status}`}>
                  {item.status === "success" && <Check size={14} />}
                  {item.status === "failed" && <CircleAlert size={14} />}
                  {item.status === "uploading" && <LoaderCircle size={14} className="animate-spin" />}
                  {statusLabels[item.status]}
                </div>
                {item.result && <><p className="mt-1.5 text-sm text-muted">{item.result.status === 3 ? "已提交，正在后台索引" : `已索引 ${item.result.chunk_count} 个片段`}</p><p className="mt-1 break-all text-xs text-muted">ID {item.result.document_id}</p></>}
                {item.error && <p className="mt-1.5 break-words text-sm text-[#b34636]">{item.error}</p>}
              </td>
              <td className="px-3 py-4 align-top"><Button size="icon" variant="ghost" disabled={running} onClick={() => remove(item.id)} aria-label={`移除 ${item.path}`}><Trash2 size={16} /></Button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
