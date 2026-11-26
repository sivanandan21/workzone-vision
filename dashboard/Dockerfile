FROM node:22-alpine AS builder

WORKDIR /app
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm ci

COPY dashboard/ ./
ARG VITE_API_BASE_URL=""
ARG VITE_AUTH_MODE="local_mock"
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_AUTH_MODE=$VITE_AUTH_MODE

RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
