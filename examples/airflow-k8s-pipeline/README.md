# Airflow + Kubernetes VICAR Pipeline Example

Apache Airflow + Kubernetes pipeline for event-driven VICAR terrain mesh generation. Demonstrates orchestrating stereo image processing (radiometric correction → correlation → XYZ → mesh) using ephemeral Kubernetes pods with fast worker spin-up.

## Architecture

**Components:**
- **LocalStack:** S3+SNS+SQS simulation in minikube
- **Seed Job:** Creates S3→SNS→SQS wiring, uploads stereo image pairs
- **Listener (Python):** Polls SQS, buffers stereo pairs, triggers Airflow DAGs
- **Airflow (Helm):** KubernetesExecutor, orchestrates VICAR tasks as ephemeral pods
- **VICAR pods (tig-worker):** Download from S3 → run VICAR → upload to S3 → exit
- **Storage:** S3 round-trip per task (stateless pods)

**Data Flow:**
1. Seed uploads stereo FDR pairs to LocalStack S3
2. S3 ObjectCreated → SNS → SQS (`.VIC` filter)
3. Listener polls SQS, buffers stereo pairs by frame id, triggers DAG once both L+R arrive
4. Airflow launches `ids_terrain_ncam` DAG: `rad_left/right` → `correlate` → `xyz_left/right` → `mesh_left/right`
5. Each task = ephemeral tig-worker pod (download → VICAR → upload)
6. Final outputs: `.obj/.mtl/.png` in `s3://ids-pipeline/output/<run_id>/`

## Prerequisites

- **minikube** (v1.30+, Kubernetes 1.27+)
- **kubectl**
- **Helm 3**
- **Docker** (for building listener + worker images)
- **TIG base image:** `ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource`
- **Calibration data:** VISOR M20 calibration data (see [Calibration Setup](#calibration-setup))
- **Sample data:** Stereo FDR image pairs (see [Sample Data](#sample-data))

## Setup

### 1. Start minikube

```bash
minikube start --cpus=4 --memory=8192 --driver=docker
eval $(minikube docker-env)
```

### 2. Create namespace

```bash
kubectl create namespace tig-airflow
```

### 3. Build listener image

```bash
cd k8s/listener
docker build -t ids-listener:latest .
```

### 4. Build the VICAR worker image

The base TIG image has VICAR binaries but **no AWS CLI**. Build worker image layering AWS CLI v2:

```bash
# Pull base TIG image
docker pull ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource

# Build worker (adds AWS CLI)
docker build -t tig-worker:latest -f k8s/worker/Dockerfile .

# Load into minikube
minikube image load tig-worker:latest
```

### 5. Calibration Setup

Download M20 calibration data from VISOR:

```bash
# Clone VISOR repository (contains M20 calibration)
git clone https://github.com/NASA-AMMOS/VISOR.git
cd VISOR

# Extract M20 calibration subset (~15GB: camera_models/, flat_fields/, param_files/)
# Location: VISOR/missions/m20/calibration/
```

Mount calibration into minikube (run in separate terminal, keep running):

```bash
minikube mount /path/to/VISOR/missions/m20/calibration:/mnt/calib &
```

### 6. Sample Data

#### Option A: Use your own stereo FDR pairs

Place stereo image pairs in a directory structure:
```
sample-data/
  NLM_<sol>_<frame>_<product>FDR_<id>.VIC  # Left eye
  NRM_<sol>_<frame>_<product>FDR_<id>.VIC  # Right eye
```

#### Option B: Download from PDS

```bash
# M20 NavCam images available at:
# https://pds-imaging.jpl.nasa.gov/volumes/m20.html
# Filter by: NCAM, FDR product type, stereo pairs (NLM/NRM prefix match)
```

Mount sample data into minikube:

```bash
minikube mount /path/to/sample-data:/mnt/sample-data &
```

### 7. Start required minikube mounts

Three host directories must stay mounted (run each in terminal or background):

```bash
# DAGs → Airflow scheduler/webserver/workers
minikube mount $(pwd)/dags:/mnt/dags &

# VICAR calibration → every VICAR pod at /usr/local/vicar/mars_calib
minikube mount /path/to/VISOR/missions/m20/calibration:/mnt/calib &

# Sample data → seed job
minikube mount /path/to/sample-data:/mnt/sample-data &
```

### 8. Deploy LocalStack

```bash
kubectl apply -f k8s/localstack/deployment.yaml
kubectl wait --for=condition=ready pod -l app=localstack -n tig-airflow --timeout=120s
```

### 9. Deploy Airflow

Airflow logs persist via hostPath PV/PVC (survives worker pod deletion):

```bash
# Mount host dir for Airflow logs (uid 50000 = airflow user)
minikube mount $(pwd)/airflow-logs:/mnt/airflow-logs --uid 50000 --gid 0 &

# Create ReadWriteMany hostPath PV + PVC
kubectl apply -f k8s/airflow/logs-pv.yaml
kubectl wait --for=jsonpath='{.status.phase}'=Bound pvc/airflow-logs-host --timeout=60s

# Create static webserver secret (prevents restart/logout churn)
kubectl create secret generic ids-webserver-secret-key --namespace tig-airflow \
  --from-literal=webserver-secret-key="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"

# Add Helm repo
helm repo add apache-airflow https://airflow.apache.org
helm repo update

# Install Airflow (chart 1.11.0 = Airflow 2.7.1)
helm install airflow apache-airflow/airflow --version 1.11.0 \
  -f k8s/airflow/values.yaml --namespace tig-airflow --wait --timeout=12m

# Wait for webserver
kubectl wait --for=condition=ready pod -l component=webserver -n tig-airflow --timeout=300s
```

### 10. Deploy wrapper scripts ConfigMap

```bash
kubectl apply -f k8s/airflow/vicar-wrappers-configmap.yaml
```

### 11. Run seed job

Update `k8s/seed/job.yaml` to reference your sample data mount path, then:

```bash
kubectl apply -f k8s/seed/job.yaml
kubectl wait --for=condition=complete job/ids-seed -n tig-airflow --timeout=300s
kubectl logs job/ids-seed -n tig-airflow
```

**Expected output:**
- Bucket `ids-pipeline` created
- SNS topic `ids-fdr-events` created
- SQS queue `ids-fdr-queue` created, subscribed to topic
- Files uploaded to `s3://ids-pipeline/global_cache/<sol>/<instrument>/`

### 12. Deploy listener

```bash
kubectl apply -f k8s/listener/deployment.yaml
kubectl wait --for=condition=ready pod -l app=ids-listener -n tig-airflow --timeout=120s
kubectl logs -f deployment/ids-listener -n tig-airflow
```

**Expected output:**
```
=== IDS Pipeline Listener ===
...
Received: s3://ids-pipeline/global_cache/.../NLM_...FDR_...VIC
  Matched process: ids_terrain_ncam
  Frame: ..., Eye: left
  Waiting for right eye (timeout in 300s)
Received: s3://ids-pipeline/global_cache/.../NRM_...FDR_...VIC
  Stereo pair complete
✓ Triggered DAG ids_terrain_ncam
```

### 13. Monitor DAG execution

```bash
# Port-forward Airflow UI
kubectl port-forward svc/airflow-webserver 8080:8080 -n tig-airflow

# Open http://localhost:8080 (admin/admin)
```

**OR via CLI:**
```bash
kubectl logs -l dag_id=ids_terrain_ncam --tail=100 --prefix --all-containers
```

### 14. Verify outputs

```bash
# Port-forward LocalStack
kubectl port-forward svc/localstack 4566:4566 -n tig-airflow

# List outputs
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-west-2
aws --endpoint-url=http://localhost:4566 s3 ls s3://ids-pipeline/output/ --recursive

# Download
aws --endpoint-url=http://localhost:4566 s3 cp s3://ids-pipeline/output/<run_id>/ ./outputs/ --recursive
```

## Project Structure

```
.
├── README.md
├── dags/
│   └── ids_terrain_ncam.py      # DAG: rad → correlate → xyz → mesh
├── k8s/
│   ├── localstack/
│   │   └── deployment.yaml      # S3+SNS+SQS pod
│   ├── seed/
│   │   └── job.yaml             # Setup job
│   ├── listener/
│   │   ├── Dockerfile
│   │   ├── listener.py          # SQS poller + Airflow trigger
│   │   └── deployment.yaml
│   ├── worker/
│   │   └── Dockerfile           # tig-worker (TIG base + AWS CLI)
│   └── airflow/
│       ├── values.yaml
│       ├── logs-pv.yaml
│       └── vicar-wrappers-configmap.yaml
└── wrappers/
    ├── rad_wrapper.sh           # marsrad
    ├── correlate_wrapper.sh     # marsecorr + marscor3
    ├── xyz_wrapper.sh           # marsxyz + filters
    └── mesh_wrapper.sh          # marsmesh + vicario
```

## VICAR Pipeline (per DAG run)

1. **rad_left, rad_right** (parallel): `marsrad` FDR→RAS radiometric correction
2. **correlate_left, correlate_right** (parallel): `marsecorr` + `marscor3` → disparity maps
3. **xyz_left, xyz_right** (parallel): `marsxyz` → pointcloud, `marsrfilt` + `m20filter` + `marsfilter` + `marsmask` → filters
4. **mesh_left, mesh_right** (parallel): `marsmesh` → `.obj` mesh, `vicario` → `.png` texture

**Total runtime:** ~6-7 minutes (minikube, 4 CPU)  
**Pod spin-up:** ~1-3s (image pre-pulled)

## Troubleshooting

**Pods stuck Pending:**
- Check: `kubectl describe pod <pod-name>`
- Verify image loaded: `minikube image ls | grep tig-worker`

**Wrapper script fails:**
- Check: `kubectl logs <pod-name>`
- Common issues: S3 endpoint unreachable, input not found, calibration mount missing

**DAG not visible:**
- Check DAG mount: `kubectl exec -it deployment/airflow-scheduler -- ls /opt/airflow/dags`
- Verify minikube mount running: `minikube mount $(pwd)/dags:/mnt/dags`

## Implementation Notes

- **AWS CLI checksum:** Set `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` to avoid LocalStack 3.0 `InvalidRequest` on PUT (wrappers + DAG have this)
- **VICAR exit codes:** Several tools exit non-zero on success. Wrappers use file-existence checks (`... || true` then verify output)
- **Calibration:** Wrappers set `MARS_CONFIG_PATH=/usr/local/vicar/mars_calib`; DAG mounts calib hostPath read-only
- **Helm chart pin:** Chart 1.11.0 (Airflow 2.7.1). Newer charts ship Airflow 3.x, breaking listener `/api/v1` usage
- **Log persistence:** `logs.persistence` + `existingClaim: airflow-logs-host` preserves logs after pod deletion
- **Webserver secret:** Static key prevents restart churn. Name it `ids-webserver-secret-key` (NOT `airflow-webserver-secret-key`, Helm-owned)

## References

- **TIG repository:** https://github.com/NASA-AMMOS/tig
- **VISOR calibration:** https://github.com/NASA-AMMOS/VISOR
- **M20 PDS archive:** https://pds-imaging.jpl.nasa.gov/volumes/m20.html
