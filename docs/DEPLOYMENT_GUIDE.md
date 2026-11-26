# WorkZone Vision Deployment & EC2 + Jenkins Setup Guide

## 1. AWS EC2 Deployment (`43.204.146.167` · Ubuntu 24.04.5 LTS · `t3.large`)

### A. Open AWS EC2 Security Group Inbound Ports
In AWS EC2 Console -> Instance `i-02fe491ebfcb12813` -> **Security** -> **Security groups** -> **Edit inbound rules**:
- `TCP 22` (SSH) -> Your IP (for MobaXterm)
- `TCP 80` (HTTP) -> `0.0.0.0/0` (WorkZone Dashboard & `/api/`)
- `TCP 8080` (Custom TCP) -> `0.0.0.0/0` or GitHub Webhook IPs + your IP (Jenkins UI & `/github-webhook/`)
- *(Keep PostgreSQL port `5432` internal to Docker — do NOT expose it publicly.)*

### B. One-Time Server Bootstrap in MobaXterm
SSH into `ubuntu@43.204.146.167` in MobaXterm and run:

```bash
git clone https://github.com/sivanandan21/workzone-vision.git ~/workzone-vision
cd ~/workzone-vision
bash deploy/scripts/ec2-bootstrap.sh
```

This installs Docker + Compose, Java 21 + Jenkins LTS (`jenkins.io-2026.key`), configures a 4 GB swap file, adds `ubuntu` and `jenkins` to the `docker` group, generates `/opt/workzone-vision/.env`, and prints your initial Jenkins admin password.

### C. Deploy Immediately (PostgreSQL + Backend + Dashboard)
```bash
cd ~/workzone-vision
newgrp docker << 'EOF'
bash deploy/scripts/deploy-ec2.sh
EOF
```

Open `http://43.204.146.167/` in Chrome and click **Run Demo** to populate Room 1 -> Room 2 -> Outside -> Return movement logs & snapshots directly into PostgreSQL!

---

## 2. Jenkins + GitHub Webhook Automation

1. Open Jenkins at `http://43.204.146.167:8080/`, unlock with the initial admin password, and install suggested plugins + **GitHub Integration** (`github`).
2. Create a **New Item** -> **Pipeline** named `workzone-vision`:
   - Build Triggers: check **GitHub hook trigger for GITScm polling**
   - Pipeline Definition: **Pipeline script from SCM**
   - SCM: **Git** -> Repository URL: `https://github.com/sivanandan21/workzone-vision.git` -> Branch: `*/main`
   - Script Path: `Jenkinsfile`
3. In GitHub (`https://github.com/sivanandan21/workzone-vision`) -> **Settings -> Webhooks -> Add webhook**:
   - **Payload URL**: `http://43.204.146.167:8080/github-webhook/`
   - **Content type**: `application/json`
   - **Trigger**: `Just the push event`
4. Push any commit to `main` — Jenkins automatically runs all Python + Dashboard tests inside Docker and redeploys `deploy/docker-compose.prod.yml` cleanly while preserving `workzone_pgdata`.

---

## 3. Enabling Live CCTV Cameras (`VEXDET ONNX`)

1. Export RF-DETR ONNX from `sivanandan21/VEXDET` (`vexdet export --checkpoint best.pt --model-name RFDETRNano`) and copy it onto EC2 at `/opt/workzone-vision/models/vexdet.onnx`.
2. Update `CAM_1_SOURCE` and `CAM_2_SOURCE` in `/opt/workzone-vision/.env` with your RTSP URLs, and place Student 1 & Student 2 reference photos in `/opt/workzone-vision/enrolled_students/student_1/` and `student_2/`.
3. Start the live camera worker:
   ```bash
   cd ~/workzone-vision
   docker compose --env-file /opt/workzone-vision/.env -f deploy/docker-compose.prod.yml --profile live-cameras up -d --build edge
   ```

---

## 4. Optional AWS Serverless Deployment (`aws/template.yaml`)

For deploying the Lambda + DynamoDB + S3 + SNS + Cognito backend instead of EC2 PostgreSQL, run:
```bash
cd aws && sam validate --template template.yaml && sam build && sam deploy --guided
```
