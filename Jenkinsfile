pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        timeout(time: 20, unit: 'MINUTES')
    }

    environment {
        WORKZONE_ENV_FILE = '/opt/workzone-vision/.env'
        WORKZONE_COMPOSE_FILE = 'deploy/docker-compose.prod.yml'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Python Edge, State Machine & PostgreSQL Tests') {
            steps {
                sh '''
                    docker run --rm -v "$PWD:/app" -w /app python:3.11-slim bash -c "
                        apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 &&
                        pip install --no-cache-dir -r edge/requirements.txt -r aws/lambdas/requirements.txt -r backend/requirements.txt pytest &&
                        pytest tests/ -v --tb=short
                    "
                '''
            }
        }

        stage('Dashboard Typecheck, Vitest & Build') {
            steps {
                sh '''
                    docker run --rm -v "$PWD/dashboard:/app" -w /app node:22-alpine sh -c "
                        npm ci && npx tsc --noEmit && npm test -- --run && npm run build
                    "
                '''
            }
        }

        stage('Deploy PostgreSQL + Backend + Dashboard on EC2') {
            when {
                branch 'main'
            }
            steps {
                sh 'bash deploy/scripts/deploy-ec2.sh'
            }
        }
    }

    post {
        always {
            sh 'docker image prune -f || true'
        }
    }
}
