import React from "react";
import ReactDOM from "react-dom/client";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppProvider } from "./components/provider/AppProvider";
import { KnowledgePage } from "./pages/KnowledgePage";
import { KnowledgeFileTreePage } from "./pages/KnowledgeFileTreePage";
import { RetrievePage } from "./pages/RetrievePage";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppProvider>
      <Routes>
        <Route path="/" element={<KnowledgePage />} />
        <Route path="/knowledge-bases" element={<KnowledgeFileTreePage />} />
        <Route path="/retrieve" element={<RetrievePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppProvider>
  </React.StrictMode>,
);
