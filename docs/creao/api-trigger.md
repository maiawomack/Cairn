# API Trigger

Source: https://docs.creao.ai/pro/api-trigger

## Overview

CREAO supports two API paths for running agents programmatically:

| Path | Create the key in | Key prefix | Endpoint family | Best for |
| --- | --- | --- | --- | --- |
| App-scoped API Trigger | The agent detail page in `agent.creao.ai` | `capi_` | `https://agent.creao.ai/api/v1/apps/{appId}/runs` | Calling one specific agent from a script or backend |
| CREAO CLI | Developer Console at `developer.creao.ai` | `cr_sk_` | `https://developer.creao.ai/v1/*` | Account-level backend integrations, async runs, realtime streaming, webhooks, and usage analytics |

Both paths run CREAO agents you own and consume credits from your CREAO account balance. CREAO CLI can also create personal agents, update agents by replacing provided fields, and request natural-language edits. They are current supported paths with different key scopes, route families, and request shapes.

For the CREAO CLI endpoint reference, see the [API Reference](https://docs.creao.ai/api-reference/overview). For a side-by-side contract summary, see [API Paths](https://docs.creao.ai/api-reference/api-paths).

API keys are server-side secrets. Do not put `capi_` or `cr_sk_` keys in browser frontend code, mobile apps, public repositories, client logs, or analytics events.

## Which path should I use?

Use the app-scoped API Trigger when you want a key that can run only one agent. This is the path shown from the agent page in `agent.creao.ai`, and it uses `inputs` in the request body.

Use CREAO CLI when your backend owns an account-level integration. CREAO CLI keys can create, edit, and run personal agents owned by the same account, use `agent_id` and `input`, and support async polling, realtime NDJSON streaming, webhooks, and account-level analytics.

## App-scoped API Trigger

Create an app-scoped API key from the agent detail page in `agent.creao.ai`. The key starts with `capi_` and is scoped to that agent.

### Create a run

```
curl -X POST "https://agent.creao.ai/api/v1/apps/00000000-0000-4000-8000-000000000000/runs" \
  -H "Authorization: Bearer capi_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": {
      "company": "Apple",
      "year": "2025"
    }
  }'
```

The response is accepted for background execution:

```
{
  "id": "11111111-1111-4111-8111-111111111111",
  "status": "pending",
  "appId": "00000000-0000-4000-8000-000000000000",
  "appName": "Research Agent"
}
```

### Poll a run

```
curl "https://agent.creao.ai/api/v1/apps/00000000-0000-4000-8000-000000000000/runs/11111111-1111-4111-8111-111111111111" \
  -H "Authorization: Bearer capi_your_key_here"
```

Poll until `status` becomes `completed`, `failed`, or `cancelled`.

### Download generated files

Polling and webhook results include generated files in `result.artifacts`:

```
{
  "id": "workspaces/your-user-id/your-thread-id/files/report.pdf",
  "title": "Report",
  "type": "application/pdf",
  "url": "https://agent.creao.ai/api/v1/runs/11111111-1111-4111-8111-111111111111/artifacts/download?artifactId=workspaces%2Fyour-user-id%2Fyour-thread-id%2Ffiles%2Freport.pdf"
}
```

Fetch `url` with your API key and follow the redirect:

```
curl --fail --location "$ARTIFACT_URL" \
  -H "Authorization: Bearer $CREAO_API_KEY" \
  --output report.pdf
```

The URL requires authentication on every request. The key must belong to the run’s owner and, if agent-scoped, match the run’s agent. Knowing the URL or file ID alone does not grant access. The endpoint is `GET /api/v1/runs/{runId}/artifacts/download?artifactId={artifactId}`; pass the artifact’s exact `id` as the encoded query value.

CREAO redirects to a temporary storage URL with a maximum lifetime of one hour (underlying credentials may expire sooner). The storage URL grants access to anyone holding it until it expires. Keep it private. Only send your API key to CREAO; do not forward it to a different origin when following redirects. Curl’s `--location` strips the Authorization header on cross-origin redirects; do not use `--location-trusted`.

Save the stable CREAO URL, not the redirect destination. If a storage link expires, fetch the stable URL again to obtain a fresh redirect. Older runs also receive stable URLs when polled. Files remain downloadable only while they remain available at their recorded location; polling retains artifact metadata after a file is deleted.

| Response | Meaning |
| --- | --- |
| `302` | Follow `Location` to download the file. |
| `401` | Authentication is missing, invalid, or revoked. |
| `404 RUN_NOT_FOUND` | The run does not exist or is inaccessible to this user or agent-scoped key. |
| `404 ARTIFACT_NOT_FOUND` | The artifact is not in the run, is outside the owner’s workspace, or the file is unavailable. |
| `400 INVALID_INPUT` | The run ID or artifact parameter is malformed. |
| `500 S3_OPERATION_FAILED` | Storage could not be reached or the download link could not be created; retry the stable URL. |

Errors use the JSON envelope `{ "error": "ERROR_CODE" }`. Existing PDF quarantine restrictions also apply to these downloads.

### Receive the result by webhook

Add `webhookUrl` to the run request, and CREAO POSTs the result to that URL when the run finishes:

```
curl -X POST "https://agent.creao.ai/api/v1/apps/00000000-0000-4000-8000-000000000000/runs" \
  -H "Authorization: Bearer capi_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": { "company": "Apple", "year": "2025" },
    "webhookUrl": "https://your-server.example/webhooks/creao"
  }'
```

`webhookUrl` must be a public `http` or `https` URL. Localhost, private-network, link-local, and metadata-service targets are rejected when you create the run, or skipped at delivery.

The webhook body:

```
{
  "runId": "11111111-1111-4111-8111-111111111111",
  "status": "completed",
  "threadId": "33333333-3333-4333-8333-333333333333",
  "result": {
    "text": "Apple reported ...",
    "artifacts": []
  }
}
```

`status` is `completed` or `failed`. `result.chatText` is added when the agent’s conversational reply differs from `result.text`. The webhook carries no error details; poll the run for its `errorCode`.

Every request carries these headers:

| Header | Value |
| --- | --- |
| `Content-Type` | `application/json` |
| `X-Creao-Delivery-Id` | A UUID that is the same on every attempt for the run’s result |

CREAO makes up to three attempts with short backoff. A non-`2xx` response, a connection error, or no response within 10 seconds is retried. Redirects are not followed, so a `3xx` response counts as a failed attempt. The first `2xx` response ends delivery.

Delivery is at least once. A retry can repeat a request your endpoint already processed, for example when it timed out before responding. Deduplicate on `X-Creao-Delivery-Id` (or `runId`), respond with a `2xx` quickly, and do slow work afterwards.

Webhooks are best-effort, so keep polling as the fallback. A run that fails before it starts executing, for example for insufficient credits, ends without a webhook.

## CREAO CLI

Create an Account API key in [Developer Console](https://developer.creao.ai/). CREAO CLI keys start with `cr_sk_` and are sent as Bearer tokens to `developer.creao.ai/v1/*`.

1

Create an Account API key

Open [Developer Console](https://developer.creao.ai/), go to **API Keys**, and create a key. Store the full key immediately because it is shown only once.

2

Find the agent ID

Start a personal-agent creation run through `POST /v1/agents` and poll it for the new `agent_id`, or open **Agents** in Developer Console and copy the `agent_id` for an existing agent. Browser-based editing, files, schedules, and debugging still live in `agent.creao.ai`.

3

Create an async run

```
curl -X POST https://developer.creao.ai/v1/runs \
  -H "Authorization: Bearer cr_sk_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "00000000-0000-4000-8000-000000000000",
    "input": {
      "company": "Apple",
      "year": "2025"
    }
  }'
```

The API returns `202 Accepted` with a run ID and conversation ID:

```
{
  "run_id": "11111111-1111-4111-8111-111111111111",
  "status": "pending",
  "agent_id": "00000000-0000-4000-8000-000000000000",
  "conversation_id": "22222222-2222-4222-8222-222222222222"
}
```

4

Poll for the result

```
curl https://developer.creao.ai/v1/runs/11111111-1111-4111-8111-111111111111 \
  -H "Authorization: Bearer cr_sk_your_key_here"
```

Poll until `status` becomes `completed`, `failed`, or `cancelled`.

## CREAO CLI reference

### Create an agent

```
POST /v1/agents
```

#### JSON body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `input` | string | Yes | Natural-language request describing the agent to create, up to 256 KB serialized |
| `conversation_id` | UUID | No | Existing creation conversation to continue |
| `webhook_url` | string | No | Public `http` or `https` URL for the terminal result |

Returns `202 Accepted` with a queued `run_id`, `conversation_id`, and `agent_id: null`. Poll `GET /v1/runs/:run_id`; when the creation run completes, the response includes the new agent ID in both `agent_id` and `result.agent_id`.

### Update an agent

```
PATCH /v1/agents/:agent_id
```

Update an agent by replacing provided fields. Omitted fields are unchanged; this endpoint does not interpret natural-language instructions. The body can include any supported create field plus optional `release_note`. The target must be a personal agent owned by the Account key owner. Team, shared, deleted, or foreign agents return `AGENT_NOT_FOUND`.

### Edit an agent from instructions

```
POST /v1/agents/:agent_id/edits
```

Start an async natural-language edit for an agent. Use this when your product wants to say what should change, for example:

```
{
  "instructions": "Add a valuation sensitivity table for the selected ticker and include a short downside-risk note.",
  "release_note": "Add valuation sensitivity section."
}
```

Returns `202 Accepted` with `run_id`, `agent_id`, `status`, and timestamps. Poll `GET /v1/agents/:agent_id/edits/:run_id` until `status` is `succeeded`, `failed`, or `cancelled`.

### Create an async run

```
POST /v1/runs
```

#### JSON body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `agent_id` | UUID | Yes | Active personal CREAO agent to run |
| `input` | JSON | Yes | Structured input for the agent, up to 256 KB serialized |
| `conversation_id` | UUID | No | Reuse an existing CREAO CLI conversation/thread |
| `webhook_url` | string | No | Public `http` or `https` URL to receive the terminal run result |

Returns `202 Accepted` with a run object.

### Get run status

```
GET /v1/runs/:run_id
```

Returns the run object for the authenticated Account key owner.

| Field | Type | Description |
| --- | --- | --- |
| `run_id` | UUID | Run ID |
| `status` | string | `pending`, `running`, `completed`, `failed`, or `cancelled` |
| `agent_id` | UUID | Agent that ran |
| `conversation_id` | UUID \| null | Conversation/thread ID for follow-up runs |
| `result` | JSON | Final agent output, present after completion |
| `usage.credits` | number | Credits used by the run, when available |
| `error.code` | string | Machine-readable failure code, when failed |
| `created_at` | string | ISO timestamp for run creation |
| `started_at` | string \| null | ISO timestamp for execution start |
| `completed_at` | string \| null | ISO timestamp for terminal status |
| `webhook_delivered_at` | string \| null | ISO timestamp for successful webhook delivery |

### List runs

```
GET /v1/runs?status=completed&limit=20&offset=0
```

Query parameters:

| Parameter | Default | Max | Description |
| --- | --- | --- | --- |
| `status` | - | - | Filter by `pending`, `running`, `completed`, `failed`, or `cancelled` |
| `limit` | `20` | `100` | Number of runs to return |
| `offset` | `0` | - | Number of runs to skip |

Response:

```
{
  "runs": [],
  "limit": 20,
  "offset": 0
}
```

## Realtime runs

Use CREAO CLI realtime runs when your backend wants streamed progress and output.

```
POST /v1/realtime/runs
```

Body fields:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `agent_id` | UUID | Yes | Active personal CREAO agent to run |
| `input` | JSON | Yes | Structured input for the agent, up to 256 KB serialized |
| `conversation_id` | UUID | No | Reuse an existing CREAO CLI conversation/thread |

Example:

```
curl -N -X POST https://developer.creao.ai/v1/realtime/runs \
  -H "Authorization: Bearer cr_sk_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "00000000-0000-4000-8000-000000000000",
    "input": { "query": "Summarize the latest quarterly report" }
  }'
```

The response is `application/x-ndjson`. Each line is one `{ "type": "..." }` object. Events include:

| Event | Meaning |
| --- | --- |
| `run.created` | Run record and conversation have been created (`render_protocol: 1`) |
| `message.created` | A new assistant message started |
| `output_text.delta` | Incremental text output |
| `thinking.started` / `thinking.delta` / `thinking.completed` | Model thinking |
| `search.started` / `search.completed` | Web or academic search card |
| `todo.updated` | Replace-all todo list |
| `tool.started` | An opaque tool call started (no input) |
| `tool.completed` | An opaque tool call completed (no output) |
| `artifact.created` | An artifact was produced |
| `run.completed` | Run finished successfully |
| `run.failed` | Run failed |
| `error` | Stream-level error |
| `ping` | Keepalive event |

## Conversations

Pass a previous `conversation_id` to continue the same thread of work with the same agent through CREAO CLI:

```
curl -X POST https://developer.creao.ai/v1/runs \
  -H "Authorization: Bearer cr_sk_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "00000000-0000-4000-8000-000000000000",
    "conversation_id": "22222222-2222-4222-8222-222222222222",
    "input": { "follow_up": "Turn that into a slide outline" }
  }'
```

## Webhooks

CREAO CLI async runs can deliver the terminal result to your backend:

```
curl -X POST https://developer.creao.ai/v1/runs \
  -H "Authorization: Bearer cr_sk_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "00000000-0000-4000-8000-000000000000",
    "input": { "query": "quarterly revenue" },
    "webhook_url": "https://your-server.example/webhooks/creao"
  }'
```

`webhook_url` must be a publicly reachable `http` or `https` URL. URLs pointing to private networks are rejected.

Delivery works the same way as on the app-scoped path: up to three attempts, no redirects followed, and one `X-Creao-Delivery-Id` across attempts. See [Webhooks](https://docs.creao.ai/developer-api/webhooks) for the CREAO CLI payload.

## Keep request shapes separate

Do not mix the two route families:

| App-scoped API Trigger | CREAO CLI |
| --- | --- |
| `Authorization: Bearer capi_...` | `Authorization: Bearer cr_sk_...` |
| `POST /api/v1/apps/{appId}/runs` | `POST /v1/runs` |
| URL-scoped `appId` | Body field `agent_id` |
| Body field `inputs` | Body field `input` |
| Agent-page key management | Developer Console key management |

## Rate limits

CREAO CLI requests are account-scoped and enforced before execution starts. The Developer Console dashboard shows your plan, available credits, and active rate limit.
