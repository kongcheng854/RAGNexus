import { Link, useLocation } from "react-router-dom";
import { BookOpen, Search, Upload } from "lucide-react";
import { useAuthMe } from "../hooks/useAuthMe";
import { errorMessage } from "../lib/http";

const pages = [
  { path: "/", label: "知识库上传", icon: Upload },
  { path: "/knowledge-bases", label: "知识库列表", icon: BookOpen },
  { path: "/retrieve", label: "检索调试", icon: Search },
];

export function AppHeader({ locked = false }: { locked?: boolean }) {
  const location = useLocation();
  const auth = useAuthMe();
  return (
    <>
    <header className="app-header">
      <span className="font-semibold text-[#233b31]">RAG 知识文件管理</span>
      <nav aria-label="页面导航" className="flex flex-wrap items-center gap-2">
        {pages.filter((page) => page.path !== location.pathname).map(({ path, label, icon: Icon }) =>
          locked ? <span key={path} aria-disabled="true" className="header-link opacity-40"><Icon size={16} />{label}</span>
            : <Link key={path} to={path} className="header-link"><Icon size={16} />{label}</Link>,
        )}
      </nav>
    </header>
    {auth.isError && <div className="auth-banner" role="alert">{errorMessage(auth.error)}</div>}
    </>
  );
}
