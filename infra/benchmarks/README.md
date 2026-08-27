# Qwen3-Embedding 4B/8B GPU runtime-matrix on Cloud Run Jobs

Runs the two entries that are currently `BLOCKED` in
`benchmarks/runtime/qwen3-embedding-who-smart-hiv-cpu-v1-report.json`
(`qwen3-embedding-4b-cpu-float32`, `qwen3-embedding-8b-cpu-float32`) as GPU
targets instead, via Cloud Run Jobs with an attached L4 in `europe-west1`.
Dimensions (2560 for 4B, 4096 for 8B) come from that existing blocked
request — not guessed here.

**STOP — do not run any command in "Deploy" below until:**
- [ ] You have a GCP project with billing enabled
- [ ] `gcloud` is installed and authenticated (`gcloud auth login`)
- [ ] You've told me the project ID to use

Everything above that line (the image, the entrypoint) is already built and
costs nothing sitting in the repo. Say the word when the project's ready and
I'll run the commands below from this session.

## What this does NOT decide for you

The bundle/suite files. `--bundle` and `--suite` in the existing CPU report
point at whatever release bundle and development suite were passed to
`corpus-steward qwen-runtime-matrix` for the sealed 0.6B run — locate those
exact files (or regenerate them with the same `corpus-steward export-*`
commands used to build that run) before the "Upload inputs" step. Passing a
different bundle/suite would seal a report against a different release,
which the schema allows but which wouldn't be comparable to the 0.6B result.

## One-time setup

```bash
PROJECT_ID=<your-project-id>
REGION=europe-west1
REPO=med-rag-benchmarks

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com storage.googleapis.com

gcloud artifacts repositories create "$REPO" \
  --repository-format=docker --location="$REGION"
gcloud auth configure-docker "${REGION}-docker.pkg.dev"

gcloud storage buckets create "gs://${PROJECT_ID}-${REPO}" --location="$REGION"
```

L4 GPU quota on Cloud Run is opt-in per project/region — if the job deploy
step below fails on quota, request it via the Cloud Console GPU quota page
before retrying; it isn't granted automatically on project creation.

## Build and push the image

Run from the repo root (`c:\Users\arsal\Projects\Med-RAG`), since the
Dockerfile's `COPY apps/api ...` is relative to that root:

```bash
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/qwen-runtime-matrix:latest"
docker build -f infra/benchmarks/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"
```

## Upload inputs

```bash
BUCKET="gs://${PROJECT_ID}-${REPO}"
gcloud storage cp <path-to-release-bundle.json> "${BUCKET}/inputs/bundle.json"
gcloud storage cp <path-to-development-suite.json> "${BUCKET}/inputs/suite.json"
```

## Deploy and run: 4B, float16

```bash
gcloud run jobs deploy qwen-embedding-4b-gpu-fp16 \
  --image="$IMAGE" \
  --region="$REGION" \
  --cpu=4 --memory=16Gi \
  --gpu=1 --gpu-type=nvidia-l4 \
  --no-gpu-zonal-redundancy \
  --max-retries=0 \
  --task-timeout=3600 \
  --set-env-vars="MODEL_ID=Qwen/Qwen3-Embedding-4B,DIMENSION=2560,DEVICE=cuda,DTYPE=float16,BATCH_SIZE=16,MATRIX_ID=qwen3-embedding-4b-gpu-fp16-v1,INPUT_BUNDLE_URI=${BUCKET}/inputs/bundle.json,INPUT_SUITE_URI=${BUCKET}/inputs/suite.json,OUTPUT_REPORT_URI=${BUCKET}/reports/qwen3-embedding-4b-gpu-fp16-v1.json"

gcloud run jobs execute qwen-embedding-4b-gpu-fp16 --region="$REGION" --wait
```

(`--no-gpu-zonal-redundancy` avoids the higher zonal-redundant GPU rate;
check `gcloud run jobs deploy --help` at deploy time in case the exact flag
name has moved.)

## Deploy and run: 8B, float16

Same shape, larger memory ceiling and smaller batch since 8B in fp16 is
~16GB of weights alone on a 24GB card:

```bash
gcloud run jobs deploy qwen-embedding-8b-gpu-fp16 \
  --image="$IMAGE" \
  --region="$REGION" \
  --cpu=4 --memory=16Gi \
  --gpu=1 --gpu-type=nvidia-l4 \
  --no-gpu-zonal-redundancy \
  --max-retries=0 \
  --task-timeout=3600 \
  --set-env-vars="MODEL_ID=Qwen/Qwen3-Embedding-8B,DIMENSION=4096,DEVICE=cuda,DTYPE=float16,BATCH_SIZE=4,MATRIX_ID=qwen3-embedding-8b-gpu-fp16-v1,INPUT_BUNDLE_URI=${BUCKET}/inputs/bundle.json,INPUT_SUITE_URI=${BUCKET}/inputs/suite.json,OUTPUT_REPORT_URI=${BUCKET}/reports/qwen3-embedding-8b-gpu-fp16-v1.json"

gcloud run jobs execute qwen-embedding-8b-gpu-fp16 --region="$REGION" --wait
```

## Fetch results back

```bash
gcloud storage cp "${BUCKET}/reports/qwen3-embedding-4b-gpu-fp16-v1.json" benchmarks/runtime/
gcloud storage cp "${BUCKET}/reports/qwen3-embedding-8b-gpu-fp16-v1.json" benchmarks/runtime/
```

Each downloaded report is a `QwenRuntimeMatrixReport` — loading it through
that Pydantic model round-trips and self-verifies `report_sha256` against
its own content, so a corrupted or hand-edited download fails to validate
rather than silently seating bad numbers into the runtime matrix.

## Teardown (avoid idle billing)

Cloud Run Jobs don't bill when not executing, but delete them once you have
what you need:

```bash
gcloud run jobs delete qwen-embedding-4b-gpu-fp16 --region="$REGION" --quiet
gcloud run jobs delete qwen-embedding-8b-gpu-fp16 --region="$REGION" --quiet
```
