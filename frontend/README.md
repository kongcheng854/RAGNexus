# RAGNexus 管理页面

独立前端项目，使用 React 18、TypeScript、Vite、Tailwind CSS、shadcn/ui 风格的本地组件、Radix Dialog、React Router v6、React Query、axios、React Hook Form 和 Zod。

## 启动

需要 Node.js 20.19+ 或 22.12+。在本目录执行以下命令。

```powershell
npm install
Copy-Item .env.example .env
npm run dev
```

先在项目根目录使用 `python scripts/create_tenant.py --id tenant_demo --name "演示租户"` 注册租户，再用 `python scripts/create_api_key.py --tenant-id tenant_demo --name "前端调试"` 生成 Key。将终端仅显示一次的明文填写到 `frontend/.env` 的 `API_KEY`，替换示例值，再启动前端。已有租户可直接签发 Key，已有 `.env` 时保留文件并编辑对应配置。

打开终端显示的地址，默认 http://127.0.0.1:5173。后端默认连接 http://127.0.0.1:8000，可修改 `.env` 的 `API_PROXY_TARGET`。修改环境变量后重启前端并刷新浏览器。Vite 开发和预览服务均通过同源代理访问现有接口。`VITE_API_BASE_URL` 留空使用 `/api/v1`；填写时使用完整 API 前缀，例如 `https://your-host/api/v1`。同名 shell 环境变量及 `.env.local` 可能覆盖 `.env` 中的设置。

请求拦截器自动加入 `Authorization: Bearer <key>`，Key 留空时省略该请求头。后端返回 `code=20010` 时，HTTP 200 与 401 均会转换为“API Key 无效或未配置，请检查 frontend/.env 中的 API_KEY”，并在页面显示。后端设为 `AUTH_ENABLED=false` 且前端 `API_KEY` 留空时，可使用服务端固定的 `tenant_demo` 调试。

`API_KEY` 按此方案编译进浏览器代码，页面访问者能读取该 Key，应按租户调试凭证管理。前端 `.env` 已忽略提交，浏览器存储仅保存知识库快捷记录。发布构建前修改配置并重新构建，构建产物中的 Key 随产物一起更新。[Vite 环境变量说明](https://vite.dev/config/shared-options#envprefix)

## 使用

三个页面通过公共顶栏相互导航。`/knowledge-bases` 从后端读取当前租户的数据，按“知识库 → 文档”两级展示，支持按知识库名称搜索、展开收起和复制知识库 ID。标题右侧通过 `/auth/me` 展示租户名称与 ID。文档层显示成功记录和实际片段数，空知识库也会展示。从列表点击「去上传」或「去检索」，URL 自动带入 `kb_id`。

上传页 `/` 创建知识库时填写名称和描述，成功后自动选中。租户由 Key 决定并只读展示。原有浏览器快捷记录按当前租户筛选，也可使用列表跳转或「使用已有 ID」选中后端知识库。

选择多个文件或选择整个文件夹，点击「开始上传」。文件夹内文件逐个提交，目录结构显示在队列里。每份文件通过现有 JSON 上传接口提交完整正文，成功表示后端同步索引完成。页面逐项展示成功、失败原因、文档 ID 和片段数。失败后继续处理后续文件。

本期支持 UTF-8 编码的 `.txt`、`.md` 文件，每个文件最多 100,000 个字符。文件夹中的其他格式会逐项显示失败。上传中锁定租户、知识库和队列，刷新页面会触发离开提醒。队列与文件内容保存在页面内存，重新打开页面需重新选择文件。失败文件修正后可重新选择并上传。网络中断时后端可能已经处理成功，请结合响应请求 ID 或后端记录确认后再重传。

## 检索调试

`/retrieve` 默认使用 hybrid，`top_k=5`，两路召回数量均为 20，`rrf_k=60`。填写知识库 UUID 和问题后发起检索，界面展示片段原文、各阶段分数和排名、文档与片段 ID。参数名称与评分指标旁的问号可悬停或键盘聚焦查看说明，Escape 可收起提示。Hybrid 子参数仅在 hybrid 模式下显示。请求固定使用 `user_id=debug_user`，所有写请求省略 `tenant_id`。

勾选重排时发送 `rerank_options.enabled=true` 和 `top_n`，未勾选时省略该字段，由后端默认配置决定是否重排。运行摘要直接读取响应 `metadata` 中的模式、召回数量、模型名、降级信息和 `latency_ms`。该耗时由服务端计算，覆盖知识库校验、召回与重排阶段，日志落库耗时另计。

## 构建和验证

```powershell
npm test
npm run build
npm run preview
```

构建输出为 `dist`。生产部署时让站点服务器将 `/api` 代理到后端，并为前端路由设置 SPA 回退。可参考本目录的 `nginx.conf.example`，将静态文件路径与后端地址改为部署环境。Vite 的 `API_PROXY_TARGET` 用于开发和预览，静态构建由部署服务器负责代理。

## 目录

`components/provider` 提供请求和路由上下文，`components/ui` 保存可复用 UI 组件，`pages` 承载页面，`services` 封装 API，`hooks` 管理本地记录和上传队列，`types` 定义接口类型，`lib` 配置请求和样式工具，`utils` 处理文件读取与顺序上传。
