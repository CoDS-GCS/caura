# Vertex AI credentials for the LoCoMo conv0 run

Project: **`project-6daf51c7-b33c-415b-954`** · location **`global`**.
(This project's `gemini-3.x` models 404 on the regional `us-central1` endpoint but
are served from `global` — verified by a tiny call; see run_locomo.sh `--check`.)

Every Gemini call in this run goes through Vertex AI (billing Google Cloud
credits): the Caura server's **memory creation** (platform LLM) and the harness
**reader** + **headline judge**. Entity extraction stays on AI Studio (the server
has no Vertex option there). gpt-4o second judge stays on OpenAI.

Vertex auth is **Application Default Credentials (ADC)** — project + location, no
API key. **Service-account keys are blocked by org policy
(`iam.disableServiceAccountKeyCreation`)**, so both consumers use the operator's
**user ADC** (`authorized_user`) at
`~/.config/gcloud/application_default_credentials.json`, whose
`quota_project_id` is already `project-6daf51c7-b33c-415b-954`.

| consumer | how it gets ADC |
|---|---|
| the harness (reader/judge, on the VM) | user ADC at the default path — read automatically; **do not** set `GOOGLE_APPLICATION_CREDENTIALS` |
| Caura `core-api` (in Docker) | the same ADC file **mounted read-only** into the container, `GOOGLE_APPLICATION_CREDENTIALS` → the mounted path |

## 1. In the Google Cloud console / gcloud (project `project-6daf51c7-b33c-415b-954`)

1. **Enable the Vertex AI API**: `gcloud services enable aiplatform.googleapis.com --project project-6daf51c7-b33c-415b-954`
   (or console → APIs & Services → enable "Vertex AI API").
2. **User ADC with a quota project** (one-time, already done on this VM):
   ```bash
   gcloud auth application-default login
   gcloud auth application-default set-quota-project project-6daf51c7-b33c-415b-954
   ```
   This writes `~/.config/gcloud/application_default_credentials.json`
   (`type: authorized_user`, `quota_project_id` set). No SA key needed, so the
   `iam.disableServiceAccountKeyCreation` policy is not involved.

## 2. Point Caura `core-api` at Vertex (memory creation)

Mount the **ADC file** (not a key) and set the platform-LLM env via the override,
then restart **only** core-api:

```bash
cd /shared_mnt/loubna/caura
LOCOMO_VERTEX_ADC=$HOME/.config/gcloud/application_default_credentials.json \
docker compose -f docker-compose.yml -f docker-compose.override.yml \
  -f experiments/harness/locomo/docker-compose.vertex.yml up -d core-api
```

This sets `PLATFORM_LLM_PROVIDER=vertex`, `PLATFORM_LLM_MODEL=gemini-3.1-flash-lite`,
the project/location/quota-project, `ENTITY_EXTRACTION_PROVIDER=gemini`, and mounts
the ADC read-only at `/secrets/adc.json` with `GOOGLE_APPLICATION_CREDENTIALS`
pointing there. After it restarts, `core-api` logs
`Platform LLM: vertex/gemini-3.1-flash-lite`. `run_locomo.sh --check` asserts all
of this and **fails if memory creation could fall back to OpenAI**.

The ADC file is mounted from `$HOME` and is **never copied into the repo**;
`.gitignore` covers `application_default_credentials.json` / `adc*.json` / `secrets/`.

## 3. Harness ADC (reader / judge, on the VM)

Nothing to do — `run_locomo.sh` exports `GOOGLE_CLOUD_QUOTA_PROJECT` and the
google-genai client reads the user ADC from `~/.config/gcloud` automatically.
`GOOGLE_APPLICATION_CREDENTIALS` is deliberately left unset (pointing it at a
missing SA key would break ADC).

## 4. Verify (free, then one tiny paid call)

```bash
bash experiments/scripts/run_locomo.sh --check     # free: no paid calls
```

The first paid action is `run_locomo.sh`'s Vertex **probe** at ingest start: one
tiny call per Gemini model (`gemini-3.8-flash`, `gemini-3.1-flash-lite`),
recording the exact model/version/region/generation-settings Vertex reports into
`manifest.json`. **If Vertex rejects the stable `gemini-3.1-flash-lite` name in
the location, the probe fails and the run stops before ingest** — it does not
silently fall back to `-preview` or another model name. `--check` now makes the
same real tiny call per model, so a 404 is caught before a run starts. Tell me if
that happens.
