# RAGNexus

RAGNexus 为业务方提供统一的知识库、文档索引和向量检索服务。上传请求等待索引完成后返回。检索响应包含排序后的文本片段、文档引用和相似度，业务方可将这些结果交给自己的生成模型或编排服务。

## 技术栈

Python 3.11、FastAPI、Pydantic v2、SQLAlchemy 2.0、Alembic、PostgreSQL 16、pgvector，以及用于 BM25 召回的 Elasticsearch 8.17.10 与 IK。文档和查询共享一个 OpenAI-compatible Embedding Provider。

## 启动

在项目根目录复制配置并编辑 `.env`。PowerShell 使用 `Copy-Item .env.example .env`，Bash 使用 `cp .env.example .env`。将 `MODEL_BASE_URL`、`MODEL_API_KEY` 和 `EMBEDDING_MODEL` 设为实际提供方的配置。模型地址填写 API 基础路径，例如 `https://api.openai.com/v1`。

```bash
docker compose up --build -d
docker compose logs -f api
```

Compose 在 PostgreSQL 就绪后运行 `alembic upgrade head`，创建 `vector` 扩展、业务表和鉴权表，再从 `http://localhost:8000` 提供 API。Compose 内的 API 使用容器服务名 `postgres` 连接数据库。

在宿主机运行 API 时，使用 `.env` 中的 `DATABASE_URL`，并运行以下命令。也可将它指向已有的 PostgreSQL 16 + pgvector 实例。首次迁移时，数据库账号需要有权限创建 `vector` 扩展，或由管理员预先启用该扩展。

```bash
docker compose up -d --wait postgres
python -m venv .venv
```

PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活虚拟环境，Bash 使用 `source .venv/bin/activate`。激活后安装依赖、执行迁移并启动服务。

```bash
python -m pip install -e .
alembic upgrade head
alembic current
python -m app
```

在 Compose 环境中可运行 `docker compose exec api alembic current` 查看当前迁移版本。

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

四个业务接口通过统一的 `get_current_tenant` 获取租户。创建、上传与检索请求已移除 `tenant_id`，额外传入该字段会被忽略。请求 Schema 与内部业务参数分开，服务层和检索日志始终使用认证后的租户 ID。跨租户的知识库访问返回知识库不存在。树形接口只返回当前租户，`keyword` 改为在当前租户内匹配知识库名称。

已有知识库、文档与检索日志在迁移中保留。为历史数据原有的租户 ID 注册租户并签发 Key 后，可继续使用该租户的数据。本地调试可显式设置 `AUTH_ENABLED=false`，此时所有请求统一使用 `tenant_demo`，`/auth/me` 可在租户表尚无该记录时返回占位身份。

前端通过 `frontend/.env` 中的 `API_KEY` 自动添加 Bearer 请求头，三个页面使用服务端租户身份，列表按知识库名称搜索。配置步骤与浏览器构建中的 Key 管理方式见 [前端说明](frontend/README.md)。

错误码 `20010` 已用于 UNAUTHORIZED，原“请求内容过大”调整为 `20012`。

## API

接口统一返回 `{"code": 0, "msg": "success", "data": ...}`。POST 接口接收 JSON，树形查询使用 GET 参数。失败时使用相应的 HTTP 状态码、非零 `code` 和错误说明。内部资源 ID 为 UUID，租户由 API Key 决定，`user_id` 由业务方传入用于日志。树形查询、上传和检索均限定当前租户。

| 方法和路径 | 请求字段 | 成功时的 `data` |
| --- | --- | --- |
| `GET /api/v1/auth/me` | Bearer 请求头 | 当前租户名称、ID 与 Key 前缀、名称 |
| `POST /api/v1/knowledge-bases/create` | `name`、可选 `description` | `kb_id`、`name`、`tenant_id`、`created_at` |
| `GET /api/v1/knowledge-bases/tree` | 可选查询参数 `keyword`，在当前租户内按知识库名称子串匹配 | 租户、知识库与成功文档的三级树，包含文档片段数 |
| `POST /api/v1/documents/upload` | `kb_id`、`title`、纯文本 `content` | `document_id`、`kb_id`、`status`、`chunk_count` |
| `POST /api/v1/rag/retrieve` | `kb_id`、`user_id`、`query`、可选 `top_k`、`retrieval_options` 和 `rerank_options` | `query`、`kb_id`、`retrieved_chunks`、`metadata` |

树形查询只含当前租户，知识库和文档按创建时间倒序，文档状态统一返回 `"SUCCESS"`。检索响应的 `metadata.latency_ms` 与检索日志共享服务端耗时，覆盖知识库校验、召回与重排阶段，日志落库耗时另计。前端的 `/knowledge-bases` 和 `/retrieve` 分别提供列表与检索调试入口，详见 [前端使用说明](frontend/README.md)。

用以下 PowerShell 命令依次调用接口。把脚本保存为 UTF-8 时，Windows PowerShell 5.1 应使用带 BOM 的编码，或直接在 PowerShell 7 中执行。

```powershell
$base = 'http://localhost:8000/api/v1'

function Send-RagRequest($path, $body) {
    $json = $body | ConvertTo-Json -Compress
    Invoke-RestMethod -Method Post -Uri "$base/$path" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $json
}

$kb = Send-RagRequest 'knowledge-bases/create' @{
    name = '退款政策知识库'
}
$kbId = $kb.data.kb_id

Send-RagRequest 'documents/upload' @{
    kb_id = $kbId
    title = '退款政策'; content = '用户可在订单完成后 7 天内申请退款。'
}

$result = Send-RagRequest 'rag/retrieve' @{
    kb_id = $kbId
    user_id = 'user_demo'; query = '退款需要几天内申请？'
}
$result | ConvertTo-Json -Depth 8
```

文档状态为 `1`（成功）、`2`（失败）或 `3`（处理中）。切片和成功状态在同一事务提交。索引失败时记录错误，响应的 `data` 包含 `document_id` 和状态 `2`。上传文本上限为 100,000 字符，查询上限为 2,000 字符。`CHUNK_SIZE` 和 `CHUNK_OVERLAP` 均按字符计数。

每个召回片段包含 `document_id`、`chunk_id`、`title`、`content` 和余弦相似度 `score`。响应元数据包含 `top_k` 和 `vector_store`。服务在指定知识库内执行精确向量检索，默认按向量分数降序返回，并记录成功检索的查询、召回引用和耗时。每个部署使用同一套 embedding 模型配置。更换模型时，用新模型重新入库，保持查询与索引使用同一向量空间。

## Elasticsearch BM25 与混合召回

`POST /api/v1/rag/retrieve` 支持 `retrieval_options`。省略时使用 `RETRIEVAL_MODE`，默认值为 `vector`。可指定 `vector`、`bm25`、`hybrid`。向量模式执行现有链路。BM25 模式直接按关键词检索，混合模式并发执行查询向量召回和 BM25 查询，再按片段 UUID 做 RRF 融合。

```json
{
  "kb_id": "替换为实际知识库 UUID",
  "user_id": "user_demo",
  "query": "ZX999 错误码如何处理？",
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

`metadata.retrieval` 记录检索模式、融合参数、实际召回数量、合并后的总片段数及降级状态。`fused_count` 是去重后、截取候选前的数量。ES 故障时，混合模式返回原向量顺序和原向量 `score`，并标记 `degraded=true`、`degraded_reason="bm25 search failed"`。向量链路失败会使混合请求失败，纯 BM25 模式下 ES 失败返回错误码 `30006`。

### 启用与已有数据回填

Compose 使用 Elasticsearch 8.17.10，并在镜像构建时安装同版本的 IK 中文分词插件。`title` 和 `content` 使用 `ik_max_word` 建索引、`ik_smart` 查询，BM25 查询对标题使用 `title^2`，并按租户和知识库过滤。ES 需要约 512 MiB Java 堆及额外进程内存，数据保存在 `es_data`。插件版本和配置方式可参考 [IK 官方说明](https://github.com/infinilabs/analysis-ik)。

在 `.env` 中将 `RETRIEVAL_MODE=hybrid`，然后先启动数据库和搜索服务，等待就绪后启动 API。

```bash
docker compose up --build -d --wait postgres elasticsearch
docker compose up --build -d api
```

宿主机 API 使用 `ELASTICSEARCH_URL=http://localhost:9200`，Compose 内自动使用 `http://elasticsearch:9200`。可用配置项见 `.env.example`，ES 请求超时使用 `ELASTICSEARCH_TIMEOUT_SECONDS`。

全局模式为 `hybrid` 或 `bm25` 时，上传成功要求 PostgreSQL 与 ES 双写完成。两处使用同一个 chunk UUID，ES 中保存文本、租户与知识库信息及元数据。Bulk 写入逐项检查，并等待刷新可检索后返回。全局模式为 `vector` 时，上传及检索均可在 ES 离线时使用。单次请求切换到 BM25 或 hybrid 前，需要确保当前知识库已经同步到 ES。[Elasticsearch 刷新行为](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/refresh-parameter)

对于已有成功文档，或在向量模式下上传的文档，运行以下命令进行回填。命令使用已有片段和 UUID，重复执行会覆盖同一索引记录。可选 `--tenant-id` 和 `--kb-id` 限定范围。

```bash
docker compose exec api python -m app.reindex_keyword
# 宿主机虚拟环境
python -m app.reindex_keyword --tenant-id tenant_demo
```

双写中的错误会回滚数据库切片、记录文档失败状态，并尝试清理 ES。两种存储之间使用补偿机制，发生进程中断或清理故障后可能留下 ES 记录。关键词召回会根据 PostgreSQL 中成功文档的当前片段再次校验租户、知识库和可见性，残留记录会被过滤。恢复后可重跑回填。已有同名索引的字段映射必须匹配 IK 配置，若不匹配，可设置一个新的 `ELASTICSEARCH_INDEX` 并回填。

关键日志包含 `HYBRID_SEARCH_START`、`VECTOR_SEARCH_SUCCESS`、`BM25_SEARCH_SUCCESS`、`BM25_SEARCH_FAILED`、`RRF_FUSION_SUCCESS` 和 `HYBRID_SEARCH_DEGRADED`，记录租户、知识库、查询、召回上限、数量和耗时。来源、排名和分数随混合结果写入现有检索日志的片段 JSON。

## 大模型重排

`POST /api/v1/rag/retrieve` 支持可选的 `top_k` 和 `rerank_options`。复制 [.env.example](.env.example) 中的模型配置到 `.env`，填写实际的 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`。重排默认关闭。设置 `RERANK_ENABLED=true` 可全局开启，也可用单次请求的 `rerank_options.enabled` 覆盖开关。`RERANK_PROVIDER=noop` 始终使用向量排序。

用已有知识库发送一次重排请求。

```powershell
$result = Send-RagRequest 'rag/retrieve' @{
    kb_id = $kbId
    user_id = 'user_demo'; query = '退款需要几天内申请？'
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

错误响应保留 `code`、`msg`、`data`，其中 `code` 为整数，`msg` 是对外提示。前端按 `code` 分支，排查时用响应头的请求 ID 查询日志。参数校验失败通过 `data.errors` 返回字段信息，索引失败继续返回文档 ID 和状态 `2`。

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
| `migrations`、`tests` | Alembic 迁移和验收测试 |

后续可通过 `IndexingService` 接入 BackgroundTasks、Celery 或消息队列。检索层可通过 `KeywordSearchProvider` 接入 OpenSearch，或实现 Milvus / Qdrant VectorStore。模型能力可通过 `LLMProvider` 增加厂商适配，通过 `RerankProvider` 增加重排策略。业务侧可扩展 ACL 过滤、评测集与反馈闭环。

## 验证

运行 `python -m unittest discover -s tests -v` 验证切片、模型协议、重排、接口和错误响应。要运行数据库集成测试，先用 Alembic 迁移专用测试库，再将 `RAGNEXUS_TEST_DATABASE_URL` 设为该库的 URL。测试写入带独立随机租户 ID 的记录，模拟 embedding 和 Chat Completions 响应，以验证真实 pgvector 检索、重排降级、检索日志和事务回滚。

混合检索集成测试还需要独立的 Elasticsearch 8.17.10 + IK 实例。设置 `RAGNEXUS_TEST_ELASTICSEARCH_URL` 后，测试会创建随机索引，验证实际 BM25、RRF、重排、ES 降级和双写失败可见性，并删除该测试索引。
