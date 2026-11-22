# Deploying to Google Cloud Run

Three Cloud Run services, built and deployed by Cloud Build on every push to `main`:

```
Browser ──► rag-ui (Streamlit, public)
               │  Google-signed ID token (service-to-service IAM)
               ▼
            rag-api (FastAPI + LangGraph, PRIVATE) ──► Qdrant Cloud, Gemini embeddings,
               │                                       Neon Postgres (chat history)
               │  LiteLLM master key
               ▼
            rag-gateway (LiteLLM, public URL, key-protected) ──► Groq
```

| Service | Image | Memory | Access |
|---|---|---|---|
| `rag-gateway` | `docker/gateway.Dockerfile` | 1 GiB | Public URL, every call needs the LiteLLM master key |
| `rag-api` | `docker/api.Dockerfile` | 2 GiB | **Private** — only the UI's service account can invoke it |
| `rag-ui` | `docker/ui.Dockerfile` | 512 MiB | Public (this is your demo link); optional Google sign-in |

All services scale to zero (`min-instances=0`) and are capped at one instance (`max-instances=1`),
which keeps the demo inside the free tier and caps the damage if someone abuses the link.
API keys live in **Secret Manager**, never in images or the repo.

---

## 0. Before you start (on your laptop)

1. **Re-lock dependencies** — `pyproject.toml` now has dependency groups (eval-only packages are kept
   out of the production image):
   ```bash
   uv lock
   uv sync          # local install is unchanged: all groups are installed by default
   ```
2. **Test the production images locally** (needs Docker Desktop running):
   ```bash
   docker compose --profile full up --build
   ```
   This also starts a local Postgres, so chats are saved: ask a question, restart with
   `docker compose --profile full restart api`, refresh the page — the chat is still there.
   Open http://localhost:8501 and ask a question. If this works, Cloud Run will too.
   Stop with `Ctrl+C`, then `docker compose --profile full down`.
3. **Commit and push everything** (including `uv.lock` and the new files):
   ```bash
   git add .
   git status        # make sure NO .env file is listed
   git commit -m "Add Cloud Run deployment: Dockerfiles, Cloud Build pipeline, docs"
   git push
   ```

---

## 1. Create the Google Cloud project (browser, ~5 min)

1. Go to https://console.cloud.google.com → create a project (e.g. `k8s-rag-assistant`).
2. **Billing** → link a billing account (a card is required even for free-tier usage).
3. **Billing → Budgets & alerts → Create budget** → e.g. ₹200/month, alerts at 50% / 90% / 100%.
   *Strongly recommended: you get an email long before any real cost.*
4. Open **Cloud Shell** (the `>_` icon, top right). It has `gcloud`, `git` and `docker` preinstalled,
   so all commands below work the same from a Mac or Windows laptop.

---

## 2. One-time setup (paste into Cloud Shell)

```bash
# ---- settings ----
export PROJECT_ID="k8s-rag-assistant"     # <- your project ID (shown on the console home page)
export REGION="us-central1"               # Cloud Run free tier is priced on Tier-1 regions like this one
gcloud config set project "$PROJECT_ID"

# ---- enable APIs ----
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com secretmanager.googleapis.com iam.googleapis.com

# ---- container registry ----
gcloud artifacts repositories create rag-app \
  --repository-format=docker --location="$REGION" \
  --description="Kubernetes RAG assistant images"
```

### 2a. Secrets (values are typed hidden, never stored in shell history)

```bash
create_secret() {
  read -rsp "Value for $1: " v; echo
  printf '%s' "$v" | gcloud secrets create "$1" --data-file=- 2>/dev/null \
    || printf '%s' "$v" | gcloud secrets versions add "$1" --data-file=-
}

create_secret groq-api-key
create_secret groq-fallback-api-key   # no second key? paste the same Groq key again
create_secret gemini-api-key
create_secret qdrant-api-key
create_secret qdrant-url              # full URL ending in :443, e.g. https://xxxx.aws.cloud.qdrant.io:443

# Production gateway key: random, not the local "sk-local-dev-key"
printf 'sk-%s' "$(openssl rand -hex 24)" | gcloud secrets create litellm-master-key --data-file=-
```

### 2a-bis. Database for chat history (Neon, free)

1. Sign up at https://neon.com (free plan, no card) → **Create project**
   - Region: **AWS US East (N. Virginia)** — closest to the `us-central1` Cloud Run region.
2. On the project dashboard click **Connect**, turn **Connection pooling OFF** (use the *direct*
   connection), and copy the connection string. It looks like
   `postgresql://user:password@ep-xxxx.us-east-1.aws.neon.tech/neondb?sslmode=require`
3. Store it as a secret:
   ```bash
   create_secret database-url
   ```
The API creates its tables on first start — nothing to run by hand.
Neon's free compute scales to zero when idle and wakes on the next query.

### 2b. Service accounts — one identity per service, least privilege

```bash
SA="iam.gserviceaccount.com"
for sa in rag-gateway-sa rag-api-sa rag-ui-sa rag-deployer; do
  gcloud iam service-accounts create "$sa"
done

# Gateway may read only the Groq keys + master key
for s in groq-api-key groq-fallback-api-key litellm-master-key; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:rag-gateway-sa@$PROJECT_ID.$SA" --role=roles/secretmanager.secretAccessor
done

# API may read only what it uses
for s in gemini-api-key qdrant-api-key qdrant-url litellm-master-key database-url; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:rag-api-sa@$PROJECT_ID.$SA" --role=roles/secretmanager.secretAccessor
done

# The UI reads no secrets; the pipeline grants it permission to call the private API.

# CI/CD identity used by Cloud Build
for role in roles/run.admin roles/iam.serviceAccountUser roles/artifactregistry.writer \
            roles/logging.logWriter roles/storage.objectViewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:rag-deployer@$PROJECT_ID.$SA" --role="$role" --condition=None
done
```

---

## 3. First deployment (manual, from Cloud Shell)

```bash
git clone https://github.com/<your-github-username>/kubernetes-rag-assistant.git
cd kubernetes-rag-assistant

gcloud builds submit --config cloudbuild.yaml \
  --service-account="projects/$PROJECT_ID/serviceAccounts/rag-deployer@$PROJECT_ID.iam.gserviceaccount.com"
```

The first build takes ~10–15 minutes (the API image installs all dependencies and bakes in the
guardrail + reranker models). At the end it prints the three URLs — the **UI URL is your demo link**.

> Your Qdrant collection already contains the ingested documents, so there is nothing to ingest.

---

## 4. CI/CD: auto-deploy on every push (browser, ~5 min)

1. Console → **Cloud Build → Triggers → Connect repository** → GitHub → authorise →
   select `kubernetes-rag-assistant`.
2. **Create trigger**
   - Event: *Push to a branch*, branch: `^main$`
   - Configuration: *Cloud Build configuration file*, location: `cloudbuild.yaml`
   - Service account: `rag-deployer@<project-id>.iam.gserviceaccount.com`
3. Save. From now on `git push` → build → deploy. Watch builds under **Cloud Build → History**.

---

## 5. Verify

```bash
UI_URL=$(gcloud run services describe rag-ui --region=$REGION --format='value(status.url)')
API_URL=$(gcloud run services describe rag-api --region=$REGION --format='value(status.url)')
GW_URL=$(gcloud run services describe rag-gateway --region=$REGION --format='value(status.url)')

curl -s "$GW_URL/health/liveliness"                           # -> "I'm alive!"
curl -s -o /dev/null -w "%{http_code}\n" "$API_URL/health"     # -> 403  (private: correct!)
echo "Demo: $UI_URL"
```

Then open the UI and run a few test prompts (a CronJob question, an off-topic one, a jailbreak).

**Check that chat history persists:** ask a question, note the chat in the sidebar, then force a
fresh API instance and reload the page — the chat must still be there:
```bash
gcloud run services update rag-api --region=$REGION --update-env-vars=RESTART_MARKER=$(date +%s)
```
Chats are tied to the page link (`?u=...`): bookmark it to find your chats again.

**First request after idle is slow (cold start):** all three services scale to zero, so the first
visitor waits roughly 20–60 s while the gateway and API start. Later requests are fast. Before a demo
or interview, open the link once to warm it up.

---

## 6. Optional: "Sign in with Google" (guest mode stays)

Visitors can still chat immediately as guests. Signing in ties their chats to their Google account,
so they appear on any device; a signed-in user who opens an old guest link is offered a button to
move those chats into the account. It uses Streamlit's built-in OIDC login (`st.login`) — Google
handles passwords and 2FA; the app never sees them.

### 6a. Create the Google OAuth client (browser, ~10 min)
1. Console → search **"Google Auth Platform"** (a.k.a. *OAuth consent screen*) → **Get started**:
   app name `Kubernetes RAG Assistant`, your support email, audience **External**, contact email → Create.
2. **Audience → Publish app**. While the app is in *Testing*, only listed test users can sign in.
   The app only asks for basic profile/email scopes, which don't require Google verification.
3. **Clients → Create client** → type **Web application** → under **Authorized redirect URIs** add both:
   - `http://localhost:8501/oauth2callback` (local)
   - `<your rag-ui URL>/oauth2callback` (find it with
     `gcloud run services describe rag-ui --region=$REGION --format='value(status.url)'`)
4. Copy the **Client ID** and **Client secret**.

### 6b. Try it locally
Create `.streamlit/secrets.toml` in the project root (already git-ignored — never commit it):
```toml
[auth]
redirect_uri = "http://localhost:8501/oauth2callback"
cookie_secret = "paste the output of: openssl rand -hex 32"
client_id = "xxxx.apps.googleusercontent.com"
client_secret = "xxxx"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```
Restart the UI (`uv run streamlit run ui/app.py`). The sidebar now shows **Sign in with Google**.
Without this file the app runs exactly as before (guest mode only).

### 6c. Enable it on Cloud Run (Cloud Shell)
```bash
export PROJECT_ID="$(gcloud config get-value project)"; export REGION="us-central1"
UI_URL=$(gcloud run services describe rag-ui --region=$REGION --format='value(status.url)')
read -rp  "Google client ID: " CID
read -rsp "Google client secret: " CSECRET; echo

cat > /tmp/streamlit-auth.toml <<EOF
[auth]
redirect_uri = "$UI_URL/oauth2callback"
cookie_secret = "$(openssl rand -hex 32)"
client_id = "$CID"
client_secret = "$CSECRET"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
EOF
gcloud secrets create streamlit-auth --data-file=/tmp/streamlit-auth.toml && rm /tmp/streamlit-auth.toml

# The UI may read it; the pipeline may check that it exists (metadata only, not the value)
gcloud secrets add-iam-policy-binding streamlit-auth \
  --member="serviceAccount:rag-ui-sa@$PROJECT_ID.iam.gserviceaccount.com" --role=roles/secretmanager.secretAccessor
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:rag-deployer@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/secretmanager.viewer --condition=None
```
Redeploy (push a commit, or **Run** the trigger). The build log shows `Google sign-in: enabled`, and
Cloud Run mounts the secret as `/app/.streamlit/secrets.toml` in the UI container.

**How identity flows:** the UI derives the owner ID from the user's Google account
(`g_` + SHA-256 of the OIDC `sub` claim) and sends it to the private API, which only the UI's service
account can call. Guest links can never use the `g_` prefix, so a guest link can't impersonate an
account, and chats can only be moved *from* a guest link *into* an account.

---

## Day-2 operations

| Task | Command |
|---|---|
| API logs | `gcloud run services logs read rag-api --region=$REGION --limit=100` |
| Gateway logs | `gcloud run services logs read rag-gateway --region=$REGION --limit=100` |
| Rotate a key | `create_secret groq-api-key` (adds a new version), then redeploy: re-run the trigger or `gcloud builds submit ...` |
| Change models / routing | edit `litellm_config.yaml`, push |
| Pause everything | services scale to zero on their own; nothing to do |
| Remove everything | `gcloud projects delete $PROJECT_ID` |

### Optional: Logfire tracing in production
```bash
create_secret logfire-token
for sa in rag-api-sa rag-ui-sa; do
  gcloud secrets add-iam-policy-binding logfire-token \
    --member="serviceAccount:$sa@$PROJECT_ID.iam.gserviceaccount.com" --role=roles/secretmanager.secretAccessor
done
```
Then append `,LOGFIRE_TOKEN=logfire-token:latest` to the `--set-secrets` of the API in
`cloudbuild.yaml`, and add `--set-secrets="LOGFIRE_TOKEN=logfire-token:latest"` to the UI deploy.

### Cost notes
- Cloud Run: scale-to-zero + `max-instances=1` keeps demo traffic within the monthly free tier.
- Artifact Registry stores every build's images (~1–2 GB per build). Add a cleanup policy
  (Artifact Registry → `rag-app` → *Cleanup policies* → keep the 3 most recent versions) so storage
  stays within the small free allowance.
- Groq / Gemini / Qdrant use your existing free-tier accounts.
- Check current pricing pages before relying on exact free-tier numbers — they change.
