# Runtime evidence

This directory stores digest-sealed runtime reports for exact corpus releases, development
suites, model revisions, artifact inventories, adapter parameters, and execution environments.
These reports are engineering evidence only; they do not consume the sealed holdout and are not
clinical validation.
# Qwen CPU runtime measurements

Runtime-matrix targets are local-only and artifact-bound. Measurements separate model
initialization, the first cold query, and warm query/document batches. The primary optimization
path is Qwen3-Embedding-0.6B exported as an int8 OpenVINO feature-extraction IR; the 4B and 8B
variants are intentionally absent from the blocking matrix.

Install the optional CPU runtime, export without a network model reference, then seal every IR
byte and its tool versions:

```powershell
python -m pip install -e ".\apps\api[cpu-optimized]"
python scripts\export_qwen_openvino.py `
  --source models\local\qwen3-embedding-0.6b `
  --output models\local\qwen3-embedding-0.6b-openvino-int8 `
  --parameters-output models\configs\qwen3-embedding-0.6b-openvino-int8-parameters.json `
  --query-instruction "Given a clinical evidence retrieval query, retrieve relevant guideline passages that answer the query."
corpus-steward model-artifact-manifest `
  models\local\qwen3-embedding-0.6b-openvino-int8 `
  --kind DENSE `
  --model-id Qwen/Qwen3-Embedding-0.6B `
  --revision 97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3 `
  --dimension 1024 `
  --adapter-id med-rag/qwen3-embedding-openvino `
  --adapter-revision 1.0.0 `
  --adapter-parameters models\configs\qwen3-embedding-0.6b-openvino-int8-parameters.json `
  --output data\local\model-manifests\qwen3-embedding-0.6b-openvino-int8.json
```

Pin the emitted artifact SHA-256 in the runtime matrix before measurement. Retrieval accuracy
must then be evaluated with a newly sealed vector batch and the same 275-case development suite;
an optimized runtime is not allowed to reuse the float32 vector-batch attestation.
