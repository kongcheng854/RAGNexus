# RAGNexus

RAGNexus 是面向多租户的文档索引与检索服务。调用方创建知识库、上传文档，索引就绪后，通过 `POST /api/v1/rag/retrieve` 获取排序后的原文片段、文档引用和分数。前端调试台可查看召回来源、改写和降级信息，离线测评工具可比较两种检索配置的质量与成本。

| 我要做什么 | 从哪里开始 |
| --- | --- |
| 部署 API 和索引 Worker | [启动](#启动) |
| 用租户 Key 完成首次上传与检索 | [租户接入指南](docs/tenant-onboarding.md) |
| 在浏览器中管理文档、调试检索 | [前端调试台](frontend/README.md) |
| 选择检索预设或查询接口字段 | [检索预设与套餐](#检索预设与套餐) · [API](#api) |
| 比较检索配置、查看实验结论 | [离线测评](#离线测评) |

## 技术栈

后端使用 Python 3.11+、FastAPI、SQLAlchemy、Alembic 和 PostgreSQL 16。Celery 通过 Redis 调度索引任务，pgvector 提供向量检索，Elasticsearch 8.17.10 与 IK 提供中文 BM25 检索。文档和查询使用同一 Embedding 模型。前端使用 React、TypeScript 和 Vite，离线质量评分使用 RAGAS。

## 启动

首次启动时，在项目根目录复制配置并编辑 `.env`。已有 `.env` 的部署直接编辑该文件。PowerShell 使用 `Copy-Item .env.example .env`，Bash 使用 `cp .env.example .env`。将 `MODEL_BASE_URL`、`MODEL_API_KEY` 和 `EMBEDDING_MODEL` 设为实际提供方的配置。模型地址填写 API 基础路径，例如 `https://api.openai.com/v1`。

```bash
docker compose up --build -d
docker compose ps
```

Compose 在 PostgreSQL 就绪后运行 `alembic upgrade head`，创建 `vector` 扩展、业务表和鉴权表，再从 `http://localhost:8000` 提供 API。Compose 内的 API 使用容器服务名 `postgres` 连接数据库。需要查看启动日志时，运行 `docker compose logs -f api worker`。

在宿主机运行 API 时，使用 `.env` 中的 `DATABASE_URL`，并运行以下命令。也可将它指向已有的 PostgreSQL 16 + pgvector 实例。首次迁移时，数据库账号需要有权限创建 `vector` 扩展，或由管理员预先启用该扩展。

```bash
docker compose up -d --wait postgres redis elasticsearch
python -m venv .venv
```

PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活虚拟环境，Bash 使用 `source .venv/bin/activate`。激活后安装依赖、执行迁移并启动服务。

```bash
python -m pip install -e .
alembic upgrade head
alembic current
python -m app
```

宿主机部署时，在另一个已激活同一虚拟环境的终端运行 Worker：

```bash
celery -A app.celery_app worker --loglevel=info
```

Windows 本机调试可追加 `--pool=solo`。API 和 Worker 需要相同的数据库、Redis、ES 和模型配置。
Compose 会一并启动 Worker。可用 `docker compose exec redis redis-cli ping` 检查 Broker，
用 `docker compose logs -f worker` 查看索引任务。数据库里的文档状态是轮询依据。

在 Compose 环境中可运行 `docker compose exec api alembic current` 查看当前迁移版本。

## 可选的检索追踪与反馈

默认 `LANGFUSE_ENABLED=false`。启用前先运行 `alembic upgrade head`。每次成功检索都会生成本地 `metadata.log_id`。启用追踪后，API 将查询及其改写、检索阶段统计、最终片段 ID 与分数发送到配置的 Langfuse 项目，收到确认后返回对应的 `trace_id`。

1. 在项目 `.env` 中设置 `LANGFUSE_DB_PASSWORD`、`LANGFUSE_NEXTAUTH_SECRET`、`LANGFUSE_SALT` 和 `LANGFUSE_ENCRYPTION_KEY`。首次自托管时为每一项分别执行 `python -c "import secrets; print(secrets.token_hex(32))"` 生成独立值，并保存好这些配置。
2. 运行 `docker compose up -d langfuse`。在 `http://localhost:3000` 创建项目并签发一组项目密钥。
3. 将项目密钥填入服务端 `.env` 的 `LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY`，设置 `LANGFUSE_ENABLED=true`。宿主机 API 使用 `LANGFUSE_HOST=http://localhost:3000`，Compose 中的 API 默认通过 `http://langfuse:3000` 访问。使用其他实例时相应设置 `LANGFUSE_HOST` 或 `LANGFUSE_DOCKER_HOST`。
4. 重启宿主机 API，或执行 `docker compose up -d --build api`。执行一次检索后，在 Langfuse 中按 `metadata.trace_id` 的值找到 `rag_retrieve`；在调试台提交评分后，可在该 trace 的 Scores 查看 `user_feedback`。

反馈请求示例见[租户接入指南](docs/tenant-onboarding.md#6-检索反馈)。部署参数见 [Langfuse v2 自托管文档](https://langfuse.com/self-hosting/v2/deployment-guide)。

## 离线测评

**当前可验证 Query Rewrite 的实验配置，也可用六道已标注的电商种子题试跑检索。** 配置引用的正式数据集 `eval/datasets/golden_basic_20.json` 尚未随仓库发布。仓库内的三份电商示例原文覆盖六条规则，正式 20 题仍需依据实际已索引原文核对，并绑定真实知识库 UUID。[实验配置](eval/experiments/query_rewrite.json) · [电商种子题](eval/datasets/ecommerce_retrieval.json) · [种子题试跑步骤](#反馈候选与历史测评)

从项目根目录运行以下命令，检查两组参数是否只相差声明的字段。这一步可在业务服务和裁判模型均离线时执行。

```bash
python -m pip install -e .
python scripts/run_eval.py validate-experiment --experiment eval/experiments/query_rewrite.json
```

### 正式 Golden Set

正式集按版本放在 `eval/datasets/<version>.json`。根节点的 `knowledge_bases` 保存每个别名对应的 `kb_id` 和 `corpus_version`，运行器按每道题的 `kb_alias` 路由。首版 `basic` 套件按 ID 列出 20 道题，其中直接事实题 10 道、口语改写题 5 道、跨章节或跨文档题 5 道。`all` 选择该版本全部题目。

取得正式文件后，用以下命令校验全量题目、标准答案、套件引用和题型配比。校验只依赖 Python 标准库。

```bash
python scripts/run_eval.py validate-dataset --dataset eval/datasets/golden_basic_20.json
```

核对答案时可用 `expected_documents` 和 `expected_headings` 记录文档、章节。增删题目或修改问题、标准答案时另存数据集版本。语料规则变化时同步更新语料版本。更换切块方式、索引或 Embedding 模型后，记录新的 `index_id` 并重跑两组。

### 两组对照实验

仓库中的 Query Rewrite 实验使用 `custom` 预设，两组都采用向量召回，关闭同义词扩展和重排。baseline 关闭改写，candidate 开启改写。运行前在 `decision` 中声明主指标、门槛及理由。

运行此实验需要已校验的 Golden Set、索引就绪的业务知识库、有改写权限的专业套餐租户，以及支持聊天补全的裁判模型。在当前终端设置租户环境变量 `RAGNEXUS_API_KEY`。`RAGNEXUS_BASE_URL` 默认指向 `http://127.0.0.1:8000`。业务后端需配置改写模型。评测进程从环境或项目 `.env` 读取 `LLM_MODEL` 和成对的 `LLM_BASE_URL`、`LLM_API_KEY`，裁判地址与凭据也可回退到 `MODEL_BASE_URL`、`MODEL_API_KEY`。裁判会接收问题、标准答案和召回片段。

```bash
python -m pip install -e ".[eval]"
python scripts/run_eval.py run --experiment eval/experiments/query_rewrite.json
```

`run` 先通过 `auth/me` 检查功能权限和当日剩余配额，再分组检索、评分并生成报告。默认零预热，业务和裁判并发各为 4。首版 20 题对应 40 次正式业务请求。设置 `warmup_cases` 会额外使用检索配额。两组共享限速规则，单题超时或返回 429 后记录错误，后续题目继续执行。业务请求携带 `X-RAGNexus-Offline-Eval: true`，后端按请求停用 Langfuse 并保留本地检索日志。裁判初始化前关闭 RAGAS 遥测，单项评分默认超时 120 秒，可用 `ragas_concurrency` 和 `ragas_timeout_seconds` 调整裁判并发和超时。

### 运行产物与评分重放

每次 `run` 打印独立目录，默认位于 `eval/results/<run_id>/`。

| 文件 | 用途 |
| --- | --- |
| `report.md`、`comparison.json` | 阅读启用建议，或按 `verdict` 和 `checks` 汇总多个实验 |
| `baseline.raw.jsonl`、`candidate.raw.jsonl` | 按题查看原文片段、排名、实际配置、客户端耗时、调用数及异常 |
| `baseline.metrics.json`、`candidate.metrics.json` | 核对同一评分批次的逐题分数和组内汇总 |
| `manifest.json`、`experiment.snapshot.json`、`dataset.snapshot.json` | 核对知识库、索引、模型、词表与改写设置摘要、运行条件、验收门槛及文件指纹 |

更换裁判后可对两组已保存的上下文重新评分。以下 PowerShell 命令使用 `run` 打印的目录。`score` 只调用裁判，`report` 在本地读取已保存的分数并核对原始记录指纹。只需重新生成报告时，直接执行最后一条命令。

```powershell
$runDir = Read-Host '输入 run 打印的运行目录'
python scripts/run_eval.py score --run-dir "$runDir"
python scripts/run_eval.py report --run-dir "$runDir"
```

重放使用旧指标口径的对照实验时，更新后的产物写入该运行目录的 `analysis-v2/`。按单一 Profile 评测正式数据集仍可使用 `run_eval.py run --dataset`，该入口支持 `--suite`、`--index-id`、`--settings-snapshot` 和 `--output`。

### 如何读实验报告

报告开头给出一个结论，再列出质量变化、附带成本和证据。表中变化统一按 candidate 减 baseline 计算。下表门槛来自仓库中的 Query Rewrite 配置，属于预设验收条件。

| 公共指标 | 统计口径 | Query Rewrite 门槛 |
| --- | --- | --- |
| Context Recall | RAGAS 根据标准答案衡量召回上下文的信息覆盖，变化以百分点表示 | 主指标至少提升 3 个百分点 |
| Context Precision | RAGAS 衡量有用上下文在返回顺序中的排名，变化以百分点表示 | 最多下降 2 个百分点 |
| P95 延迟 | 对成功正式请求的客户端耗时做线性插值，展示毫秒差和相对增幅 | 增幅最多 40% |
| 平均应用模型调用数 | 对成功请求中有准确计数的题目取均值，计入业务改写和重排调用 | 每题平均最多增加 1 次 |

P95 从取得并发名额和速率许可后、发出请求前开始计时，到读完响应为止。应用模型调用数只统计正式检索中的改写与重排，预热、Embedding 和裁判调用另计。缺少耗时或计数时，报告保留已有质量分数，并注明成本统计的有效样本数。少于 3 道有效配对题的切片只列逐题分数。

| `comparison.json` 的 `verdict` | 启用建议 |
| --- | --- |
| `effective` | 有效，建议启用 |
| `effective_high_cost` | 有效但成本较高，建议按场景启用 |
| `no_clear_benefit` | 无明显收益，暂不启用 |
| `regression` | 出现负向效果，建议回退 |
| `execution_failed` | 评测执行失败，暂时不能下结论 |

先检查两组结果和运行条件是否完整。正式请求或评分失败、目标能力降级、成本不完整或原始记录无法核实时，启用结论为 `execution_failed`。完整实验先检查质量回退，再判断主指标收益，最后核对成本。可按报告中的题目 ID 回到两组原始记录，比较改善或退化最大的题目。

### 反馈候选与历史测评

在项目根目录执行 `python -m pip install -e ".[eval]"`。在 `.env` 中设置支持聊天补全的裁判模型 `LLM_MODEL`（省略时为 `gpt-4o-mini`）。裁判优先使用成对设置的 `LLM_BASE_URL` 和 `LLM_API_KEY`，否则使用 `MODEL_BASE_URL` 和 `MODEL_API_KEY`。裁判会收到问题、片段正文和标准答案。每条有标准答案的测例都会以 `eval_runner` 重新请求检索，消耗该租户的检索配额。

1. **导出候选。** 使用上节的 Langfuse 项目密钥。以下 Bash 命令假定 `KB_ID` 是真实知识库 UUID，`RAGNEXUS_API_KEY` 是所属租户的 Key。导出最近 30 天评分严格小于 3 的检索，保留 trace ID、日志 ID 和反馈备注。省略 `--kb-id` 会导出项目内各库的候选。

   ```bash
   python scripts/export_eval_cases_from_langfuse.py --days 30 --max-score 3 --kb-id "$KB_ID" \
     --output eval/datasets/imported_from_feedback.json
   ```

2. **人工审核。** 在导出文件的 `ground_truth` 中摘录对应知识库的原文。重新导出会替换候选文件，请先合并要保留的标注。下面的命令直接读取已审核文件，按同一知识库内的问题去重，并保留主集中已有的标准答案。正式业务请使用与该库原文对应的主集。

   ```bash
   python scripts/export_eval_cases_from_langfuse.py --input eval/datasets/imported_from_feedback.json \
     --kb-id "$KB_ID" --merge eval/datasets/ecommerce_retrieval.json
   ```

   省略 `--input` 并指定 `--merge` 时，脚本会重新查询 Langfuse，并为本次命中的同库同题沿用默认候选文件中已审核的答案。

3. **运行并比较。** 首次试跑可以直接使用 6 条已标注的电商种子。将[三份示例原文](data/ecommerce/)各作为一篇文档上传到示例知识库，等索引就绪后，用 `--kb-id` 绑定该库。也可以把 `--dataset` 指向审核后的反馈文件。预设优先使用命令行的 `--profile`，其次是数据集的 `default_profile`，两者均省略时为 `balanced`。免费套餐请使用 `speed`。

   ```bash
   python scripts/run_retrieval_eval.py --dataset eval/datasets/ecommerce_retrieval.json \
     --kb-id "$KB_ID" --api-key "$RAGNEXUS_API_KEY" --profile balanced --output eval/reports/before.json
   # 修改该知识库的同义词后，用相同的测例和裁判再跑一次
   python scripts/run_retrieval_eval.py --dataset eval/datasets/ecommerce_retrieval.json \
     --kb-id "$KB_ID" --api-key "$RAGNEXUS_API_KEY" --profile balanced --output eval/reports/after.json
   ```

4. **对照单库与联查。** 将 `data/ecommerce/` 中的原文上传到电商库，将 [客服示例规则](data/customer_service/会员补偿与工单.md) 上传到独立客服库。索引完成后，使用标准或专业套餐运行下面的对照。两个文件使用相同的问题和标准答案，仅检索范围不同。请把 `KB_ID` 和 `SERVICE_KB_ID` 设为实际的电商库和客服库 UUID。

   ```bash
   mkdir -p eval/reports
   jq --arg a "$KB_ID" --arg b "$SERVICE_KB_ID" '.cases[].kb_ids = [$a,$b]' \
     eval/datasets/multi_kb_cases.json > eval/reports/joint-input.json
   jq --arg a "$KB_ID" '.cases[].kb_ids = [$a]' \
     eval/reports/joint-input.json > eval/reports/single-input.json
   for scope in single joint; do
     python scripts/run_retrieval_eval.py --dataset "eval/reports/${scope}-input.json" \
       --api-key "$RAGNEXUS_API_KEY" --output "eval/reports/${scope}-report.json"
   done
   ```

每条已评测记录的 `kb_ids` 和 `multi_kb` 标明实际检索范围。测例优先取自身的 `kb_ids`，单库测例仍可取自身或数据集的 `kb_id`。导出反馈时，`--kb-id` 可匹配联查范围中的任一库，导出的测例保留完整列表。合并标注按有序知识库列表和问题去重。

报告给出逐题分数和等权平均分。`context_precision` 衡量有用片段是否排在前面，`context_recall` 衡量片段能覆盖多少标准答案。召回低于 0.5 的问题会单独列出。空标注记为 `skipped` 并从均值中排除；已标注但检索成功返回零个片段时两项都计 0。切换预设时为每次运行指定不同的 `--output`。报告位于已忽略的 `eval/reports/`。[测例目录](eval/datasets/)

## API Key 与租户鉴权

`AUTH_ENABLED=true` 为默认配置。先运行 `alembic upgrade head`，应用 `0002_tenant_auth`，再通过两个独立脚本注册租户、签发 Key。脚本从项目根目录执行并使用 `.env` 中的数据库配置。

```bash
python scripts/create_tenant.py --id tenant_demo --name "演示租户"
python scripts/create_api_key.py --tenant-id tenant_demo --name "本地开发"
# 可选有效期，使用带时区的 ISO 8601 时间
python scripts/create_api_key.py --tenant-id tenant_demo --name "短期测试" --expires-at "2026-12-01T00:00:00Z"
```

Compose 中可使用 `docker compose exec api python scripts/create_tenant.py ...` 和 `create_api_key.py ...`。相同租户 ID 重复注册会失败，租户不存在或已禁用时无法签发 Key。Key 形如 `rk_live_<32 位随机 hex>`，仅在成功签发后的终端输出一次。数据库保存 SHA-256 hash、短前缀、名称和有效期，日志仅记录 Key ID 与前缀。脚本输出应由签发者保存。

调用方在每个请求头中发送 `Authorization: Bearer <API Key>`。把签发的 Key 设置为当前 shell 的 `RAGNEXUS_API_KEY` 后，可以通过 `/auth/me` 验证身份。

```powershell
$base = 'http://localhost:8000/api/v1'
$headers = @{ Authorization = "Bearer $env:RAGNEXUS_API_KEY" }
Invoke-RestMethod -Uri "$base/auth/me" -Headers $headers
```

返回的 `data` 包含 `tenant_id`、`tenant_name`、`key_prefix`、`key_name`。校验覆盖 Key 是否存在、状态为 active、未过期及租户状态为 active。撤销 Key（`api_keys.status=revoked`）或禁用租户（`tenants.status=disabled`）后，后续请求会被拒绝。缺少或无效 Key 均返回 HTTP 401、`code=20010` 和 `WWW-Authenticate: Bearer`。

业务接口通过统一的 `get_current_tenant` 获取租户。创建、上传与检索请求已移除 `tenant_id`，额外传入该字段会被忽略。请求 Schema 与内部业务参数分开，服务层和检索日志始终使用认证后的租户 ID。跨租户的知识库访问返回知识库不存在。树形接口只返回当前租户，`keyword` 改为在当前租户内匹配知识库名称。

已有知识库、文档与检索日志在迁移中保留。为历史数据原有的租户 ID 注册租户并签发 Key 后，可继续使用该租户的数据。本地调试可显式设置 `AUTH_ENABLED=false`，此时所有请求统一使用 `tenant_demo`，`/auth/me` 可在租户表尚无该记录时返回占位身份。

前端通过 `frontend/.env` 中的 `API_KEY` 自动添加 Bearer 请求头，三个页面使用服务端租户身份，列表按知识库名称搜索。配置步骤与浏览器构建中的 Key 管理方式见 [前端说明](frontend/README.md)。

错误码 `20010` 已用于 UNAUTHORIZED，原“请求内容过大”调整为 `20012`。

## 检索预设与套餐

新租户默认使用免费套餐，首次请求请显式传入 `"profile": "speed"`。服务端省略 `profile` 时按 `balanced` 校验。当前租户可用的预设和配额由 `GET /api/v1/auth/me` 返回。[租户接入指南](docs/tenant-onboarding.md#3-套餐与配额)

| `profile` | 召回方式 | `top_k` | 改写与重排 | 可用套餐 |
| --- | --- | ---: | --- | --- |
| `speed` | 向量 | 3 | 均关闭 | 免费、标准、专业 |
| `balanced` | 向量 + BM25 | 5 | 均关闭 | 标准、专业 |
| `quality` | 向量 + BM25 | 8 | 均开启 | 专业 |
| `custom` | 由请求和服务配置决定 | 可指定 | 按选项生效，开启改写或重排需专业套餐 | 标准、专业 |

## API

接口统一返回 `{"code": 0, "msg": "success", "data": ...}`。检索和知识库创建接收 JSON，文档上传支持 JSON 正文或 multipart 文件，树形查询使用 GET 参数。失败时使用相应的 HTTP 状态码、非零 `code` 和错误说明。内部资源 ID 为 UUID，租户由 API Key 决定，`user_id` 由业务方传入用于日志。树形查询、上传和检索均限定当前租户。

| 方法和路径 | 请求字段 | 成功时的 `data` |
| --- | --- | --- |
| `GET /api/v1/auth/me` | Bearer 请求头 | 当前租户名称、ID、Key 信息、套餐、功能、配额与用量 |
| `POST /api/v1/knowledge-bases/create` | `name`、可选 `description` | `kb_id`、`name`、`tenant_id`、`created_at` |
| `GET /api/v1/knowledge-bases/tree` | 可选查询参数 `keyword`，在当前租户内按知识库名称子串匹配 | 租户、知识库与各状态文档的三级树，包含文档片段数 |
| `POST /api/v1/documents/upload` | JSON 正文或 multipart 文件、知识库 ID | `document_id`、`kb_id`、`status`、`chunk_count` |
| `GET /api/v1/documents/{id}` | 文档 ID | 标题、状态、错误、片段数及时间戳 |
| `DELETE /api/v1/documents/{id}` | 成功或失败的文档 | 清理两套索引并删除记录 |
| `POST /api/v1/documents/{id}/reindex` | 有原文的成功或失败文档 | 返回处理中状态 |
| `GET /api/v1/knowledge-bases/{id}` | 知识库 ID | 名称、描述、完整 settings、文档数及时间戳 |
| `PATCH /api/v1/knowledge-bases/{id}` | name、description、settings 至少一项 | 更新后的详情，settings 整段替换 |
| `DELETE /api/v1/knowledge-bases/{id}` | 无处理中文档的知识库 | 删除关联片段、文档、检索日志及知识库 |
| `POST /api/v1/rag/retrieve` | `kb_ids` 或 `kb_id`、`user_id`、`query`、可选 `profile`，自定义时可传 `top_k`、`retrieval_options`、`query_options` 和 `rerank_options` | `query`、`kb_ids`、`kb_id`、`retrieved_chunks`、`metadata` |
| `POST /api/v1/rag/feedback` | `trace_id`、1～5 的整数 `score`、可选 `log_id` 和 `comment` | `feedback_id`、`trace_id`、`log_id`、`score` |

树形查询只含当前租户，知识库和文档按创建时间倒序，文档状态返回 `SUCCESS`、`FAILED` 或 `PROCESSING`，失败原因在 `error_message` 中。检索响应的 `metadata.latency_ms` 与检索日志共享服务端耗时，覆盖知识库校验、召回与重排阶段，日志落库耗时另计。前端的 `/knowledge-bases` 和 `/retrieve` 分别提供列表与检索调试入口，详见 [前端使用说明](frontend/README.md)。

用以下 PowerShell 命令依次调用接口。把脚本保存为 UTF-8 时，Windows PowerShell 5.1 应使用带 BOM 的编码，或直接在 PowerShell 7 中执行。

```powershell
$base = 'http://localhost:8000/api/v1'
$headers = @{ Authorization = "Bearer $env:RAGNEXUS_API_KEY" }

function Send-RagRequest($path, $body) {
    $json = $body | ConvertTo-Json -Compress
    Invoke-RestMethod -Method Post -Uri "$base/$path" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $json
}

$kb = Send-RagRequest 'knowledge-bases/create' @{
    name = '退款政策知识库'
}
$kbId = $kb.data.kb_id

$upload = Send-RagRequest 'documents/upload' @{
    kb_id = $kbId
    title = '退款政策'; content = '用户可在订单完成后 7 天内申请退款。'
}

do {
    Start-Sleep -Seconds 1
    $doc = Invoke-RestMethod -Uri "$base/documents/$($upload.data.document_id)" -Headers $headers
} while ($doc.data.status -eq 3)
if ($doc.data.status -eq 2) { throw $doc.data.error_message }

$result = Send-RagRequest 'rag/retrieve' @{
    kb_id = $kbId
    profile = 'speed'
    user_id = 'user_demo'; query = '退款需要几天内申请？'
}
$result | ConvertTo-Json -Depth 8
```

文档状态为 `1`（成功）、`2`（失败）或 `3`（处理中）。切片和成功状态在同一事务提交。API 在数据库中记录待投递标记，进程中断后会补投；重复任务通过文档行锁和状态检查跳过。上传受理时状态为 3、片段数为 0。网络等暂时性错误最多重试两次，间隔 10 秒。失败后可在状态接口读取原因。处理中删除或重建返回 HTTP 409。迁移前没有原文的处理中文档会标记为失败，需重新上传。升级前应先等旧版同步上传结束。上传文本上限为 100,000 字符，查询上限由 `QUERY_MAX_LENGTH` 配置，默认为 2,000 字符。空白查询和超长查询返回 HTTP 422、`20004`。`CHUNK_SIZE` 和 `CHUNK_OVERLAP` 均按字符计数。

上传的 `.md` 和直接粘贴的正文按一级、二级标题分节，结束当前章节时清空重叠窗口。`.txt` 文件沿用字符窗口。Markdown 表格单独索引，小表保留原文；大表重复表头，每组最多 `TABLE_MAX_ROWS_PER_CHUNK` 条数据（默认 10），同时按字符预算尽量装满。围栏代码优先整块保留，过长时按完整代码行分组。单行表格数据、代码行或标题超过字符预算时保留整行。片段元数据含 `heading_path`、`chunk_type` 及从 0 开始的 `chunk_index`，表格另有从 1 开始的 `table_part`。新规则在文档下次建索引时生效。

单库向量检索的片段包含来源 `kb_id`、`kb_name`、`document_id`、`chunk_id`、`title`、`content` 和余弦相似度 `score`。响应元数据包含 `top_k` 和 `vector_store`。服务在指定知识库内执行精确向量检索，默认按向量分数降序返回，并记录成功检索的查询、召回引用和耗时。每个部署使用同一套 embedding 模型配置。更换模型时，用新模型重新入库，保持查询与索引使用同一向量空间。

## 多格式文档上传

宿主机运行时，安装 `python -m pip install -e ".[parsers]"`，执行 `alembic upgrade head`，再启动 API 和 Celery Worker。Compose 镜像已包含解析依赖，并通过同一个 `document_uploads` 卷保存原文件。分开部署时，请为两个进程配置同一个绝对 `DOCUMENT_STORAGE_PATH` 并挂载同一目录；默认目录为 `./data/uploads`。

原 JSON 接口继续接收 `kb_id`、`title`、`content`。同一路径也接收 multipart 的 `kb_id`、`file` 和可选 `title`，默认标题取文件名去掉扩展名。单个文件默认最多 20 MiB，可通过 `DOCUMENT_MAX_SIZE_MB` 调整，并相应调整反向代理的请求体上限。

```bash
curl -fsS -H "Authorization: Bearer $RAGNEXUS_API_KEY" \
  -F "kb_id=$KB_ID" -F "file=@policy.docx" http://127.0.0.1:8000/api/v1/documents/upload
```

Worker 从 Word 提取标题、正文和表格，从文字版 PDF 按实际页码提取正文，也支持 UTF-8 的 TXT 和 Markdown。上传受理后，按文档 ID 查询最终状态及 `error_message`。扫描件需要先做 OCR；含嵌入对象或复杂文本框的 Word 请改为普通正文后另存为 DOCX。

普通重建复用已解析的正文和解析元数据。解析器升级后，使用 `POST /api/v1/documents/{id}/reindex?reparse=true` 或批量脚本的 `--reparse`，从保留的原文件重新提取。JSON 文档继续使用已保存的正文。删除文档或整库时会清理相应原文件。

在空的测试知识库运行以下命令。脚本生成并上传 DOCX、文字版 PDF、TXT 和 JSON Markdown 四份样本，等待索引完成后按关键词检索，同时核对 Word 表格和 PDF 页码；样本会留在该测试库。

```bash
python scripts/verify_parser_12.py --kb-id "$KB_ID" --api-key "$RAGNEXUS_API_KEY"
```

## 切块策略升级后 reindex

先部署使用新切块器的 Worker，确认 PostgreSQL、Elasticsearch 和 Celery Broker 可用。建议低峰期先挑一篇含标题和表格的文档试跑。脚本读取项目 `.env`，直连数据库和队列，逐篇清除旧块、重新投递并等待 Worker 写回结果。从清除旧块到新索引提交之间，该文档暂时无法召回。

```bash
# 限指定文档，可重复提供 --document-id。省略 --tenant-id 时从知识库读取
python scripts/reindex_knowledge_base.py --kb-id "$KB_ID" --tenant-id "$TENANT_ID" --document-id "$DOC_ID"

# 重建该知识库中原已成功索引的全部文档
python scripts/reindex_knowledge_base.py --kb-id "$KB_ID"
```

在 Compose 环境可在上述命令前加 `docker compose exec api`。全库模式跳过失败和处理中记录，显式指定文档时也可重试失败记录。缺少保存原文的历史文档会保留旧索引。脚本逐篇打印文档 ID，最后汇总 `success`、`failed`、`skipped`。有失败时退出码为 1。等待并发名额和等待 Worker 完成各默认 600 秒，可用 `--wait-timeout` 调整。等待超时后，已提交的任务仍可继续执行。

也可用 `POST /api/v1/documents/{id}/reindex` 重建同租户下有原文的成功或失败文档，随后查询文档状态。完成后在检索调试台输入文档里的问题。片段同时有章节路径和块类型时，卡片会在正文上方展示两者，例如「平台交易规则 / 退款时效」和 `table`。

## 多知识库联合检索

标准套餐可在一次请求中查询最多 3 个库，专业套餐最多 5 个，免费套餐限 1 个。请求示例中的 UUID 需替换为当前 API Key 所属租户的库 ID。

```json
{
  "kb_ids": ["68db18f8-5590-4f92-bf59-00e859d01918", "3c4e97b7-9bb4-4a46-ab8f-2bda1dee8347"],
  "user_id": "user_demo",
  "query": "退款时会员补偿怎么算？",
  "profile": "balanced"
}
```

使用 Bash 和 jq 可直接传入两个已建库的 UUID。

```bash
curl -fsS -H "Authorization: Bearer $RAGNEXUS_API_KEY" -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg a "$KB_ID" --arg b "$SERVICE_KB_ID" \
    '{kb_ids:[$a,$b],user_id:"user_demo",query:"退款时限和会员补偿各是什么？",profile:"balanced"}')" \
  http://localhost:8000/api/v1/rag/retrieve
```

- 请求同时包含两个选择器时以 `kb_ids` 为准。列表必须非空。套餐按提交的列表长度校验，超限返回 HTTP 403 和 `20013`。通过限额校验后，重复的 ID 按首次出现顺序合并。
- 服务先核对全部库的归属。无效归属和不存在的 UUID 均返回 HTTP 404、`20009`，`data.missing_kb_id` 指向第一个无法访问的 ID。
- 单个库可用 `kb_id` 或单元素列表，返回原有排名和分数。多库共用一次查询处理，按请求顺序合并改写上下文和同义词，向量和混合模式共用一个查询向量。
- 各库并行召回，候选上限 `per_kb_top_k = max(top_k, retrieval_options.vector_top_k, 10)`，其中 `top_k` 和选项取预设生效后的值。跨库按库内名次累计 `1 / (HYBRID_RRF_K + rank)`，默认常数为 60。同分按请求中的库顺序排列。融合后的前 `top_k` 条进入可选的统一重排。设置较小的 `rerank_options.top_n` 或 `RERANK_MAX_CANDIDATES` 时，精排选中的片段排在前面，其余片段按融合顺序补足 `top_k`。
- 响应以 `kb_ids` 表示实际检索范围，兼容字段 `kb_id` 取其首项，每个片段携带自己的 `kb_id` 和 `kb_name`。联查时片段的 `score` 为跨库 RRF 分数，`metadata.retrieval` 提供 `multi_kb`、`kb_count`、`per_kb_top_k`、`fusion` 和 `rrf_k`。
- 一次联查占用一次租户 QPS，成功后计入一次日检索量。升级时执行 `alembic upgrade head`。新检索日志与 Langfuse trace 均记录 `kb_ids` 和首库 `kb_id`，旧日志可通过 `kb_id` 读取。

## Elasticsearch BM25 与混合召回

`POST /api/v1/rag/retrieve` 的 `profile=custom` 支持手动设置 `retrieval_options`。以下示例使用专业套餐开启重排。省略时使用 `RETRIEVAL_MODE`，默认值为 `vector`。可指定 `vector`、`bm25`、`hybrid`。向量模式执行现有链路。BM25 模式直接按关键词检索，混合模式并发执行查询向量召回和 BM25 查询，再按片段 UUID 做 RRF 融合。

```json
{
  "kb_id": "替换为实际知识库 UUID",
  "user_id": "user_demo",
  "query": "ZX999 错误码如何处理？",
  "profile": "custom",
  "top_k": 20,
  "retrieval_options": {
    "mode": "hybrid",
    "vector_top_k": 20,
    "bm25_top_k": 20,
    "rrf_k": 60
  },
  "rerank_options": {
    "enabled": true,
    "top_n": 5
  }
}
```

`vector_top_k` 和 `bm25_top_k` 分别控制两路召回数量，`top_k` 控制融合后送入可选重排阶段的候选数量上限。混合与 BM25 模式下省略 `top_k` 使用 `HYBRID_TOP_N`，向量模式使用 `TOP_K`。重排继续使用 `rerank_options.top_n` 与 `RERANK_TOP_N` 控制最终返回数量。

融合按每一路从 1 开始的排名计算 `1 / (rrf_k + rank)`，同一片段两路得分相加。RRF 同分时先沿用向量顺序，再放入 BM25 独有片段。混合结果的 `score` 为 RRF 分数，同时包含原始 `vector_score`、`bm25_score`、`vector_rank`、`bm25_rank` 和 `retrieval_source`，缺少某一路的分数或排名为 `null`。启用重排后，这些字段随片段保留，`rerank_score` 作为精排分数。

`metadata.retrieval` 记录检索模式、融合参数、实际召回数量、合并后的总片段数及降级状态。`fused_count` 是去重后、截取候选前的数量。单库 hybrid 任一路失败时使用另一路的原始排序和分数。BM25 失败时 `degraded_reason` 为 `bm25 search failed`；embedding 或向量查询失败时为 `vector search failed`。两种情况都返回 HTTP 200、`degraded=true`，片段的 `retrieval_source` 标明实际来源。两路都失败返回 HTTP 503 和 `RETRIEVAL_FAILED`（`30007`）。单库纯向量和纯 BM25 模式继续返回对应依赖错误。

联查中至少一库完成召回，即可对成功库的结果做跨库 RRF。某库失败时，`metadata.retrieval` 包含 `degraded=true`、`partial_kb_success=true` 和按请求顺序列出的 `failed_kb_ids`。`per_kb_metadata` 按库 ID 记录状态和错误码名称。仅丢失某个召回通道、该库仍可返回结果时，状态为 `degraded`。全部库均失败时返回 `30007`。成功的联查计一次日配额。服务先校验套餐和所有库的归属，再执行召回。

成功且无片段时，`empty_reason=no_indexed_chunks` 表示所选库没有属于成功文档的已索引片段，`no_chunks_matched` 表示存在此类片段，本次可用的召回路径未命中。部分库失败时结合 `failed_kb_ids` 解释结果。空结果同样返回 HTTP 200。部署前执行 `alembic upgrade head`，检索日志的 `retrieval_metadata` 保存响应中的检索详情，降级与空结果也写入 Langfuse 的 retrieval span 和 trace metadata。

使用含成功索引的测试库和标准或专业套餐验证 ES 故障回退。测试 ES 已停止或已在测试部署中隔离后运行以下命令，脚本通过 API 验证返回码、降级原因和片段来源。

```bash
python scripts/verify_robustness_retrieve.py --base-url http://127.0.0.1:8000 \
  --kb-id "$KB_ID" --expect-es-down
```

认证使用环境变量 `RAGNEXUS_API_KEY`。非默认长度通过 `--query-max-length` 与目标 API 对齐。省略故障标志且未配置测试 ES 时，脚本会验证查询边界并明确跳过 ES 检查。

### 启用与已有数据回填

Compose 使用 Elasticsearch 8.17.10，并在镜像构建时安装同版本的 IK 中文分词插件。`title` 和 `content` 使用 `ik_max_word` 建索引、`ik_smart` 查询，BM25 查询对标题使用 `title^2`，并按租户和知识库过滤。ES 需要约 512 MiB Java 堆及额外进程内存，数据保存在 `es_data`。插件版本和配置方式可参考 [IK 官方说明](https://github.com/infinilabs/analysis-ik)。

在 `.env` 中将 `RETRIEVAL_MODE=hybrid`，然后先启动数据库和搜索服务，等待就绪后启动 API。

```bash
docker compose up --build -d --wait postgres elasticsearch
docker compose up --build -d api worker
```

宿主机 API 使用 `ELASTICSEARCH_URL=http://localhost:9200`，Compose 内自动使用 `http://elasticsearch:9200`。可用配置项见 `.env.example`，ES 请求超时使用 `ELASTICSEARCH_TIMEOUT_SECONDS`。

Worker 在 PostgreSQL 与 ES 写入完成后将索引状态更新为成功。两处使用同一个 chunk UUID，ES 中保存文本、租户与知识库信息及元数据。Bulk 写入逐项检查，并等待刷新可检索后返回。查询模式控制召回方式，新上传文档在所有模式下均建立两套索引。[Elasticsearch 刷新行为](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/refresh-parameter)

对于升级前仅有向量索引的成功文档，运行以下命令进行回填。命令使用已有片段和 UUID，重复执行会覆盖同一索引记录。可选 `--tenant-id` 和 `--kb-id` 限定范围。

```bash
docker compose exec api python -m app.reindex_keyword
# 宿主机虚拟环境
python -m app.reindex_keyword --tenant-id tenant_demo
```

双写中的错误会回滚数据库切片、记录文档失败状态，并尝试清理 ES。两种存储之间使用补偿机制，发生进程中断或清理故障后可能留下 ES 记录。关键词召回会根据 PostgreSQL 中成功文档的当前片段再次校验租户、知识库和可见性，残留记录会被过滤。恢复后可重跑回填。已有同名索引的字段映射必须匹配 IK 配置，若不匹配，可设置一个新的 `ELASTICSEARCH_INDEX` 并回填。

关键日志包含 `HYBRID_SEARCH_START`、`VECTOR_SEARCH_SUCCESS`、`BM25_SEARCH_SUCCESS`、`BM25_SEARCH_FAILED`、`RRF_FUSION_SUCCESS` 和 `HYBRID_SEARCH_DEGRADED`，记录租户、知识库、查询、召回上限、数量和耗时。来源、排名和分数随混合结果写入现有检索日志的片段 JSON。

## 大模型重排

升级后先在项目根目录执行 `alembic upgrade head`，为已有知识库添加默认值为 `{}` 的 `settings` 字段。
检索前可使用现有 LLM 将问题改写为检索短句。默认 `QUERY_REWRITE_ENABLED=false`，
专业套餐的 custom 请求传入 `"query_options": {"enabled": true, "strategy": "rewrite"}` 可单次开启，
`enabled: false` 可覆盖全局开启的设置。改写超时默认 2000 毫秒，可用 `QUERY_REWRITE_TIMEOUT_MS` 调整。
超时或无有效输出时回退到原话，随后仍匹配当前知识库的词表。

运维可复制 [词表示例](examples/kb_settings.example.json)，填写触发词、扩展词及可选的领域说明，
再执行 `python scripts/update_kb_settings.py --kb-id <知识库 UUID> --settings-file kb_settings.json`。
**脚本按整份文件覆盖该知识库的 settings**，写入 `{}` 即可清空。词表默认匹配当前检索句。改写关闭或降级后，仍可按原话匹配。单次请求可设置 `query_options.synonym_enabled=false` 关闭词表扩展。调试台运行摘要展示原话、改写句、追加词、最终检索句和降级原因。
`metadata.query_processing` 在无处理时为 null。检索耗时单独统计，重排和检索日志仍使用用户原话。

专业套餐的 `profile=custom` 请求支持手动设置 `top_k` 和 `rerank_options`。复制 [.env.example](.env.example) 中的模型配置到 `.env`，填写实际的 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`。重排默认关闭。设置 `RERANK_ENABLED=true` 可全局开启，也可用单次请求的 `rerank_options.enabled` 覆盖开关。`RERANK_PROVIDER=noop` 始终使用向量排序。

用已有知识库发送一次重排请求。

```powershell
$result = Send-RagRequest 'rag/retrieve' @{
    kb_id = $kbId
    user_id = 'user_demo'; query = '退款需要几天内申请？'
    profile = 'custom'
    top_k = 20
    rerank_options = @{ enabled = $true; top_n = 5 }
}
$result | ConvertTo-Json -Depth 8
```

- `top_k` 控制向量召回数量，省略时使用 `TOP_K`。请求可指定 1 到 1000。启用重排时，最多从召回结果中取前 `RERANK_MAX_CANDIDATES` 个片段，单片段送给模型的正文截断到 `RERANK_CHUNK_MAX_CHARS` 字符。响应里的正文仍为完整片段。
- `rerank_options.top_n` 控制最终返回数量上限，省略时使用 `RERANK_TOP_N`。候选数不足时返回可用结果。每个结果保留向量 `score`，有模型打分时增加 `rerank_score`。未被模型评分的候选补 0，同分沿用向量顺序。未知片段 ID 会被忽略，重复 ID 采用首个有效评分。
- `metadata.rerank` 记录是否启用、使用的模型及 Provider、目标返回数量、候选数量和是否降级。模型超时、调用失败或输出不合法时，接口仍成功返回，按 `top_n` 取原始向量顺序的结果，并标记 `degraded=true`。关闭重排时直接返回向量召回结果。默认关闭且请求未提供重排选项时沿用原响应结构。
- 启用重排的检索日志存储最终返回顺序、正文和可用评分。重排理由只可能出现在显式开启的模型日志预览中。每次重排会把查询和截断后的候选正文发送到配置的模型端点。

`LLM_PROVIDER=openai_compatible` 使用支持 JSON 对象模式的 Chat Completions 端点。Embedding 和重排分别使用各自的地址与凭证。DeepSeek 示例使用当前模型名 `deepseek-flash`。百炼可配置支持 JSON 对象模式的模型，例如非思考模式下的 `qwen-plus`，模型地址和密钥按实际工作空间填写。[DeepSeek 模型文档](https://api-docs.deepseek.com/quick_start/pricing/) · [百炼结构化输出文档](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output)

## 日志与问题排查

所有 HTTP 响应都带有 `X-Request-ID`。同一次调用的接口、模型和业务日志共享一个请求 ID。日志行包含时间、级别、模块、函数、行号和事件字段。

- `logs/app.log` 保存达到 `LOG_LEVEL` 的应用日志，`logs/error.log` 单独保存其中的 `ERROR` 和 `CRITICAL`。两份文件独立按大小轮转，默认每份 10 MiB、保留 5 个归档。终端支持彩色输出。
- `API_REQUEST` 和 `API_RESPONSE` 记录方法、URL 路径和正文字节数，响应另有状态码和耗时。请求正文随框架读取，日志在请求结束时汇总。模型日志记录模型名、调用耗时、结果摘要或错误分类。异常日志记录类型和堆栈。
- 默认只记录正文大小和模型结果摘要。需要排查具体内容时，可在受控环境设置 `LOG_PAYLOADS=true`，采集限长的 JSON 请求、响应、模型输入和模型结果预览。凭证类字段会脱敏，查询字符串、请求头和超长或非 JSON HTTP 正文会省略。启用后，请按业务数据的权限管理日志文件，因为文档内容、查询和模型结果仍可能包含敏感信息。
- 控制台和文件写入由后台线程处理。队列满时丢弃新记录，服务正常关闭时报告丢弃数量并刷盘。Compose 的默认日志目录由 `api_logs` 数据卷保存。每个进程使用独立日志目录，以免多个进程同时轮转同一组文件。

配置项见 [`.env.example`](.env.example)。查找某次失败时，从响应头复制请求 ID，然后运行以下 PowerShell 命令。

```powershell
Select-String -Path logs/app.log,logs/error.log -Pattern 'request_id=<实际请求 ID>' -SimpleMatch
```

应用模块可用标准 `logging.getLogger(__name__)`，用 `log_event(logger, logging.INFO, "DOCUMENT_INDEXED", document_id=document_id)` 附加可检索的字段。`app.api.request_logging.log_api_request` 是 ASGI 装饰器，默认由应用工厂统一装配。模型适配器用 `log_model_request` 获取开始时间，再按调用结果使用 `log_model_response` 或 `log_model_failure`。可参考 [embedding 适配器](app/providers/embedding/openai_compatible.py)。

## 错误码

错误响应保留 `code`、`msg`、`data`，其中 `code` 为整数，`msg` 是对外提示。前端按 `code` 分支，排查时用响应头的请求 ID 查询日志。参数校验失败通过 `data.errors` 返回字段信息，后台索引错误可通过文档状态接口查询。

| 范围 | 含义 | 常见错误 |
| --- | --- | --- |
| `0` | 成功 | `0` 成功 |
| `20000–29999` | HTTP 和请求 | `20004` 参数错误，`20007` 路由不存在，`20008` 方法错误，`20009` 知识库不存在 |
| `30000–39999` | 数据库和存储 | `30002` 数据重复，`30003` 数据冲突，`30004` 存储不可用，`30005` 存储超时 |
| `40000–49999` | 模型服务 | `40001` 模型超时，`40002` 无有效结果，`40003` 内容被拒，`40004` 长度超限，`40005` 限流，`40006` 模型不可用 |
| `50000–59999` | 系统和服务 | `50000` 意外异常，`50001` 服务繁忙，`50002` 文档索引失败 |

旧客户端需把知识库不存在、参数错误、路由不存在、embedding 通用失败的判断值分别更新为 `20009`、`20004`、`20007`、`40000`，并按上表处理细分模型错误。模型超时返回 HTTP 504，限流返回 429，内容和长度错误返回 422。

业务层可用 `raise_error(ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)`，或通过 `ServiceError(ErrorCode.INDEXING_FAILED, data=...)` 附带业务字段。需保留原始异常时使用 `raise ... from exc`；全局处理器把公开提示和内部堆栈分别写入响应与日志。错误码、提示和默认 HTTP 状态统一定义在 [错误码枚举](app/core/error_codes.py)。

## 目录与扩展点

| 目录 | 职责 |
| --- | --- |
| `app/api`、`app/schemas` | HTTP 路由、依赖装配、统一响应和请求校验 |
| `app/services` | 知识库创建、文档索引与状态管理、检索编排 |
| `app/repositories`、`app/models`、`app/db` | 数据存取、ORM 模型和数据库会话 |
| `app/providers`、`app/utils` | `VectorStore`、`KeywordSearchProvider`、`EmbeddingProvider`、`LLMProvider`、`RerankProvider`、`DocumentParser`、`TextSplitter` 及当前实现、UUID 生成 |
| `frontend/` | 知识库管理、上传队列和检索调试台 |
| `scripts/`、`eval/` | 租户运维与离线测评命令、数据集、实验配置和运行产物 |
| `migrations`、`tests` | Alembic 迁移和验收测试 |

Celery Worker 通过 `IndexingService` 为已保存的文档建立索引。检索层可通过 `KeywordSearchProvider` 接入 OpenSearch，或实现 Milvus / Qdrant VectorStore。模型能力可通过 `LLMProvider` 增加厂商适配，通过 `RerankProvider` 增加重排策略。业务侧可扩展 ACL 过滤、评测集与反馈闭环。

## 验证

以下测评专项测试使用临时数据和模拟客户端，覆盖正式数据集校验、并发检索、指标计算、结论判定及评分重放。

```bash
python -m pip install -e ".[test]"
python -m pytest -q tests/test_eval_dataset.py tests/test_eval_experiment.py tests/test_eval_metrics_report.py tests/test_retrieval_eval.py
```

运行 `python -m unittest discover -s tests -v` 可执行完整后端测试，验证切片、模型协议、重排、接口和错误响应。要运行数据库集成测试，先用 Alembic 迁移专用测试库，再将 `RAGNEXUS_TEST_DATABASE_URL` 设为该库的 URL。测试写入带独立随机租户 ID 的记录，模拟 embedding 和 Chat Completions 响应，以验证真实 pgvector 检索、重排降级、检索日志和事务回滚。

混合检索集成测试还需要独立的 Elasticsearch 8.17.10 + IK 实例。设置 `RAGNEXUS_TEST_ELASTICSEARCH_URL` 后，测试会创建随机索引，验证实际 BM25、RRF、重排、ES 降级和双写失败可见性，并删除该测试索引。
