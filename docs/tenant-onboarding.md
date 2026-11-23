# 租户接入指南

RAGNexus 为业务系统建立文档索引并召回相关片段。业务方从 `retrieved_chunks` 读取正文、文档 ID 和片段 ID，用自己的生成模型组织答案、展示引用。以下示例假设 API 和索引 Worker 已启动，客户端使用 Bash、curl 和 jq。部署步骤见[项目 README](../README.md)。

## 1. 开通租户

运营人员在已配置数据库连接的项目根目录执行以下命令。使用 Compose 时可在每条命令前加 `docker compose exec api`。

```bash
python scripts/create_tenant.py --id tenant_a --name "业务 A"
python scripts/create_api_key.py --tenant-id tenant_a --name "业务 A 检索客户端"
```

新租户默认为免费套餐。第二条命令仅显示一次明文 Key，请交给调用方保存在服务端。调用方可用以下命令输入 Key、验证身份并查看配额：

```bash
BASE=http://localhost:8000/api/v1
read -rsp "API Key: " RAGNEXUS_API_KEY && echo
AUTH="Authorization: Bearer $RAGNEXUS_API_KEY"
curl -fsS -H "$AUTH" "$BASE/auth/me" | jq .data
```

核对 `tenant_id`、`plan` 和 `features.allowed_profiles`。`limits` 给出套餐上限，`usage` 给出当前知识库数和当天成功检索次数。后续请求携带同一请求头即可确定租户。

## 2. 建库并上传第一份文档

```bash
KB_ID=$(curl -fsS -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"退款手册"}' "$BASE/knowledge-bases/create" | jq -er '.data.kb_id')

DOC_ID=$(curl -fsS -H "$AUTH" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg kb "$KB_ID" '{kb_id:$kb,title:"退款期限",content:"订单完成后七天内可申请退款。"}')" \
  "$BASE/documents/upload" | jq -er '.data.document_id')

curl -fsS -H "$AUTH" "$BASE/documents/$DOC_ID" | jq '.data | {status, chunk_count, error_message}'
```

状态为 `3` 时稍等后重复最后一条命令，状态为 `1` 即可检索。状态为 `2` 时查看 `error_message`，处理原因后可调用 `POST /documents/{document_id}/reindex`。

## 3. 套餐与配额

| 套餐 | 可用预设 | 混合检索 | 重排和问题改写 | 检索 QPS | 每日成功检索 | 知识库数 | 每库文档数 | 同时索引文档数 | 每次检索库数 |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| 免费 free | speed | 关闭 | 关闭 | 3 | 500 | 1 | 30 | 1 | 1 |
| 标准 standard | speed、balanced、custom | 开启 | 关闭 | 10 | 5,000 | 5 | 200 | 2 | 3 |
| 专业 pro | 全部四档 | 开启 | 开启 | 50 | 100,000 | 50 | 5,000 | 10 | 5 |

`GET /auth/me` 返回的 `data.limits.max_kb_per_retrieve` 是一次检索允许的库数。免费租户仅支持单库，标准套餐最多联查 3 个库，专业套餐最多 5 个。超过上限时返回 `20013`。一次成功联查计入一次日检索用量。

套餐决定可用能力和用量上限。每次检索再用 `profile` 选择执行方式。省略时服务端选择 `balanced`，免费租户请显式传 `speed`。日用量按北京时间零点重置，成功请求计入发起日，失败和被拒绝的请求不计数。上传和重建共用租户的并发索引额度。

## 4. 选择检索预设

以下函数复用相同的知识库、问题和认证头。先执行函数定义，再执行本套餐允许的示例。

```bash
retrieve() {
  curl -sS -H "$AUTH" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg kb "$KB_ID" --argjson options "$1" \
      '{kb_id:$kb,user_id:"demo_user",query:"什么时候能退款？"} + $options')" \
    "$BASE/rag/retrieve" | jq .
}

# 所有套餐：向量检索，取 3 个候选
retrieve '{"profile":"speed"}'

# 标准、专业：混合检索，取 5 个候选
retrieve '{"profile":"balanced"}'

# 专业：混合检索，取 8 个候选，开启改写和重排
retrieve '{"profile":"quality"}'

# 标准、专业：自定义关键词检索，取 4 个候选
retrieve '{"profile":"custom","top_k":4,"retrieval_options":{"mode":"bm25"}}'
```

三个固定预设由服务端展开。`custom` 可手动设置检索模式和候选数，重排和问题改写仍需专业套餐。查看 `data.retrieved_chunks` 获取结果，查看 `data.metadata.tenant_policy` 核对生效的预设、模式、重排和改写开关。

## 5. 配置词表

所有套餐均可维护知识库的同义词。将以下内容保存为 UTF-8 编码的 `kb-settings.json`：

```json
{
  "synonyms": [
    { "terms": ["退款", "退钱"], "expand": ["退货", "售后"] }
  ]
}
```

运营人员在服务端执行以下命令，使用第 2 步的知识库 UUID。脚本完整替换该库的 settings，编辑前可通过 `GET /knowledge-bases/{kb_id}` 读取并保留已有配置。使用容器时，配置文件须在容器内可读。

```bash
python scripts/update_kb_settings.py --kb-id "$KB_ID" --settings-file kb-settings.json
```

## 6. 检索反馈

业务方在每次检索成功后保存 `data.metadata.log_id` 和 `data.metadata.trace_id`。前者对应本地检索日志，后者用于定位 Langfuse trace。关闭追踪或追踪服务未确认接收时，检索仍返回片段与 `log_id`，`trace_id` 为 `null`。此时可继续使用结果，调试台会隐藏评分区。

以下示例复用第 4 节的 `retrieve` 函数，以当前租户的 Key 为刚才的检索提交 2 分和备注：

```bash
RESULT=$(retrieve '{"profile":"speed"}')
LOG_ID=$(jq -er '.data.metadata.log_id' <<< "$RESULT")
if TRACE_ID=$(jq -er '.data.metadata.trace_id' <<< "$RESULT"); then
  curl -sS -H "$AUTH" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg log "$LOG_ID" --arg trace "$TRACE_ID" \
      '{log_id:$log,trace_id:$trace,score:2,comment:"第三个片段更符合问题"}')" \
    "$BASE/rag/feedback" | jq .
fi
```

评分为 1～5 的整数，备注可选。推荐同时传两个 ID；只传 `trace_id` 时服务也会核对当前租户的检索日志。成功响应包含 `feedback_id`、`trace_id`、`log_id` 和 `score`。

如果反馈请求超时，业务方可在首次提交时设置可选请求头 `X-Feedback-Submission-Id`（随机 UUID），重试同一份反馈时复用该值。同一检索下相同标识对应一条评分；独立评价请使用新的标识。省略此头时每次提交各生成一条评分。调试台会自动为每次检索的反馈表单保留此标识。

调试台在每次检索结果上方提供五星评分，可填写备注后提交。调试台的运行摘要显示本地日志 ID 和已确认的 trace ID。即使某个阶段未写入，只要 Langfuse 确认主 trace，仍可提交反馈。运营人员可在 Langfuse 的 Traces 中按 `user_feedback` 分值筛选低分记录，展开 `query_processing`、`retrieval` 和 `rerank` 查看改写、召回数及降级情况。

需要量化词表或预设调整的效果时，可从 Langfuse 导出本库低分反馈，补全原文依据后运行回归。操作步骤见[离线测评](../README.md#离线测评)，基线和待审核文件见[测评目录](../eval/)。

## 7. 常见错误

| 业务码 | HTTP | 处理方式 |
|---|---:|---|
| 20010 | 401 | 检查 Bearer 头和 Key 有效期，确认租户与 Key 均已启用。 |
| 20013 | 403 | 查看 `/auth/me` 的功能列表，切换到允许的预设或关闭超出套餐的自定义功能。 |
| 20014 | 429 | 阅读 `msg`。并发索引已满时等待任务结束，日额度用完时等次日，容量已满时整理资源或联系运营升档。 |
| 20005 | 429 | 降低请求速率并退避重试。 |
| 20020 | 503 | 反馈服务不可用。请运营人员检查 Langfuse 开关、项目密钥和连通性。 |
| 20021 | 403 | 使用同一次检索返回的两个 ID，并以原租户的 Key 提交。 |
| 20022 | 422 | 将 score 设为 1～5 的 JSON 整数。 |

## 8. 旧租户与升档

升级到 `0006_tenant_plan` 时，已有普通租户迁移为 `standard`，已有 `tenant_demo` 迁移为 `pro`。关闭鉴权的本地演示模式统一使用专业套餐。运营人员可在服务端调整指定租户的套餐：

```bash
python scripts/update_tenant_plan.py --tenant-id tenant_a --plan pro
```

使用原 Key 再调用 `/auth/me`，即可核对新套餐及限额。
