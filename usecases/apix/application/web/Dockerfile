# ── build ────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
# VITE_* are baked at build time; same-origin API by default (nginx proxies /api).
ARG VITE_DASHBOARD_API_URL=""
ARG VITE_CHAT_API_URL=""
ENV VITE_DASHBOARD_API_URL=$VITE_DASHBOARD_API_URL VITE_CHAT_API_URL=$VITE_CHAT_API_URL
RUN npm run build

# ── serve ────────────────────────────────────────────────────────────────────
FROM nginx:alpine
# DASHBOARD_API_UPSTREAM is substituted into the nginx config at container start
# (nginx:alpine auto-processes /etc/nginx/templates/*.template via envsubst).
ENV DASHBOARD_API_UPSTREAM=http://dashboard-api:8000
COPY nginx.default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
