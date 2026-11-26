#!/usr/bin/env bash
set -euo pipefail

echo "=== WorkZone Vision EC2 Bootstrap (Ubuntu 24.04 LTS) ==="

# 1. Configure 4 GB swap if missing (safe memory headroom on t3.large during Docker/Jenkins builds)
if [ ! -f /swapfile ]; then
    echo "Creating 4G swapfile..."
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/apt/fstab >/dev/null
fi

# 2. Remove Java 17 if installed by an earlier run, then install OpenJDK 21 (required by Jenkins LTS)
sudo apt-get update -y
sudo apt-get purge -y 'openjdk-17-*' || true
sudo apt-get autoremove -y || true
sudo apt-get install -y ca-certificates curl gnupg wget git openssl fontconfig openjdk-21-jre-headless

if [ -x /usr/lib/jvm/java-21-openjdk-amd64/bin/java ]; then
    sudo update-alternatives --set java /usr/lib/jvm/java-21-openjdk-amd64/bin/java || true
fi
JAVA_BIN=$(readlink -f "$(command -v java)")
JAVA_HOME_DIR=$(dirname "$(dirname "$JAVA_BIN")")
echo "Using Java binary: $JAVA_BIN (JAVA_HOME=$JAVA_HOME_DIR)"
java -version

# 3. Install Docker Engine + Compose plugin (official Ubuntu apt repository)
if ! command -v docker >/dev/null 2>&1; then
    echo "Installing Docker Engine & Compose plugin..."
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    . /etc/os-release
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# 4. Prepare persistent /opt/workzone-vision directory & .env secrets BEFORE Jenkins startup
sudo mkdir -p /opt/workzone-vision/models /opt/workzone-vision/enrolled_students /opt/workzone-vision/backups
sudo chown -R ubuntu:docker /opt/workzone-vision || sudo chown -R ubuntu:ubuntu /opt/workzone-vision
sudo chmod -R 775 /opt/workzone-vision

if [ ! -f /opt/workzone-vision/.env ]; then
    PG_PASS=$(openssl rand -hex 18)
    EDGE_KEY=$(openssl rand -hex 20)
    cat <<EOF | sudo tee /opt/workzone-vision/.env >/dev/null
POSTGRES_DB=workzone_vision
POSTGRES_USER=workzone
POSTGRES_PASSWORD=${PG_PASS}
EDGE_INGEST_API_KEY=${EDGE_KEY}
VEXDET_ONNX_PATH=/app/models/vexdet.onnx
AWAY_ALERT_THRESHOLD_SECONDS=900
STRICT_TARGET_MODE=true
VITE_AUTH_MODE=local_mock
CORS_ORIGINS=*
CAM_1_SOURCE=rtsp://admin:password@192.168.1.101:554/stream1
CAM_2_SOURCE=rtsp://admin:password@192.168.1.102:554/stream1
EOF
    sudo chmod 640 /opt/workzone-vision/.env
    echo "Generated /opt/workzone-vision/.env with secure random credentials."
fi

# 5. Configure Java 21 systemd override, clear systemd failed rate-limit, and install/fix Jenkins LTS
sudo mkdir -p /etc/systemd/system/jenkins.service.d
cat <<EOF | sudo tee /etc/systemd/system/jenkins.service.d/java21.conf >/dev/null
[Service]
Environment="JAVA_HOME=${JAVA_HOME_DIR}"
Environment="JENKINS_JAVA_CMD=${JAVA_BIN}"
TimeoutStartSec=300
EOF
sudo systemctl daemon-reload
sudo systemctl reset-failed jenkins.service || true

# Recover cleanly if an earlier run left jenkins half-configured under Java 17
sudo dpkg --configure -a || true

if ! dpkg-query -W -f='${Status}' jenkins 2>/dev/null | grep -q "install ok installed"; then
    echo "Installing Jenkins LTS..."
    sudo mkdir -p /etc/apt/keyrings
    sudo wget -O /etc/apt/keyrings/jenkins-keyring.asc \
        https://pkg.jenkins.io/debian-stable/jenkins.io-2026.key
    echo "deb [signed-by=/etc/apt/keyrings/jenkins-keyring.asc] https://pkg.jenkins.io/debian-stable binary/" | \
        sudo tee /etc/apt/sources.list.d/jenkins.list >/dev/null
    sudo apt-get update -y
    sudo apt-get install -y jenkins
fi

# 6. Grant Docker access to ubuntu and jenkins users
sudo usermod -aG docker ubuntu || true
sudo usermod -aG docker jenkins || true
sudo chown -R ubuntu:docker /opt/workzone-vision
sudo chown ubuntu:docker /opt/workzone-vision/.env
sudo systemctl enable --now docker
sudo systemctl reset-failed jenkins.service || true

if ! sudo systemctl restart jenkins; then
    echo "WARNING: Jenkins failed to start. Showing recent journal logs:"
    sudo journalctl -u jenkins.service -n 40 --no-pager || true
    exit 1
fi
sudo systemctl enable jenkins

PUBLIC_IP=$(curl -s --max-time 3 http://checkip.amazonaws.com || echo "43.204.146.167")

echo ""
echo "================================================================"
echo "Bootstrap Complete!"
echo "Dashboard URL        : http://${PUBLIC_IP}/"
echo "Jenkins URL          : http://${PUBLIC_IP}:8080/"
echo "GitHub Webhook URL   : http://${PUBLIC_IP}:8080/github-webhook/"
echo "Jenkins Initial Pass : $(sudo cat /var/lib/jenkins/secrets/initialAdminPassword 2>/dev/null || echo 'Check /var/lib/jenkins/secrets/initialAdminPassword')"
echo "================================================================"
echo "Reminder: Open AWS EC2 Security Group TCP ports 22 (SSH), 80 (HTTP), and 8080 (Jenkins)."
