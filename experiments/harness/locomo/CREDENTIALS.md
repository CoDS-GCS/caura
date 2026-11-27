# Vertex AI credentials for the LoCoMo conv0 run

Project: **`project-6daf51c7-b33c-415b-954`** · region **`us-central1`**.

Every Gemini call in this run goes through Vertex AI (billing Google Cloud
credits): the Caura server's **memory creation** (platform LLM) and the harness
**reader** + **headline judge**. Entity extraction stays on AI Studio (the server
has no Vertex option there). gpt-4o second judge stays on OpenAI.

Vertex auth is **Application Default Credentials (ADC)** — project + location, no
API key. Two consumers need ADC:

| consumer | how it gets ADC |
|---|---|
| Caura `core-api` (in Docker) | a **service-account JSON key** mounted into the container, `GOOGLE_APPLICATION_CREDENTIALS` pointing at it |
| the harness (reader/judge, on the VM) | your **user ADC** via `gcloud auth application-default login` (or the same SA key via `GOOGLE_APPLICATION_CREDENTIALS`) |

## 1. In the Google Cloud console (project `project-6daf51c7-b33c-415b-954`)

1. **Enable the Vertex AI API** — APIs & Services → Enable APIs → "Vertex AI API" → Enable.
   (CLI equivalent: `gcloud services enable aiplatform.googleapis.com --project project-6daf51c7-b33c-415b-954`)
2. **Create a service account** — IAM & Admin → Service Accounts → Create:
   name e.g. `caura-locomo-vertex`.
3. **Grant it `roles/aiplatform.user`** (Vertex AI User) on the project.
   (CLI: `gcloud projects add-iam-policy-binding project-6daf51c7-b33c-415b-954 \
     --member serviceAccount:caura-locomo-vertex@project-6daf51c7-b33c-415b-954.iam.gserviceaccount.com \
     --role roles/aiplatform.user`)
4. **Create a JSON key** for that SA → download it. Place it **outside git**, e.g.:
   ```
   mkdir -p /shared_mnt/loubna/caura/secrets
   mv ~/Downloads/<key>.json /shared_mnt/loubna/caura/secrets/vertex-sa.json
   chmod 600 /shared_mnt/loubna/caura/secrets/vertex-sa.json
   ```
   `secrets/` and `*vertex*sa*.json` are gitignored — the key is never committed.

## 2. Point Caura `core-api` at Vertex (memory creation)

From the repo root, mount the key and set the platform-LLM env by layering the
override, then restart **only** core-api:

```bash
cd /shared_mnt/loubna/caura
LOCOMO_VERTEX_SA_KEY=/shared_mnt/loubna/caura/secrets/vertex-sa.json \
docker compose -f docker-compose.yml -f docker-compose.override.yml \
  -f experiments/harness/locomo/docker-compose.vertex.yml up -d core-api
```

This sets `PLATFORM_LLM_PROVIDER=vertex`, `PLATFORM_LLM_MODEL=gemini-3.1-flash-lite`,
the project/location, `ENTITY_EXTRACTION_PROVIDER=gemini`, and mounts the key at
`/secrets/vertex-sa.json`. After it restarts, `core-api` logs
`Platform LLM: vertex/gemini-3.1-flash-lite`. `run_locomo.sh --check` asserts all
of this and **fails if memory creation could fall back to OpenAI**.

## 3. Harness ADC (reader / judge, on the VM)

```bash
gcloud auth application-default login            # opens a browser/device flow
# or, to reuse the SA key non-interactively:
export GOOGLE_APPLICATION_CREDENTIALS=/shared_mnt/loubna/caura/secrets/vertex-sa.json
```

Tip: if you need to run the interactive login in this session, type it with a
leading `!` in the prompt so its output lands here:
`! gcloud auth application-default login`

## 4. Verify (free, then one tiny paid call)

```bash
bash experiments/scripts/run_locomo.sh --check     # free: no paid calls
```

The first paid action is `run_locomo.sh`'s Vertex **probe** at ingest start: one
tiny call per Gemini model (`gemini-3.8-flash`, `gemini-3.1-flash-lite`),
recording the exact model/version/region/generation-settings Vertex reports into
`manifest.json`. **If Vertex rejects the stable `gemini-3.1-flash-lite` name in
us-central1, the probe fails and the run stops before ingest** — it does not
silently fall back to `-preview`. Tell me if that happens.
