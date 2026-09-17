FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
ARG VITE_OPS_API_URL=""
ENV VITE_OPS_API_URL=$VITE_OPS_API_URL
RUN npm run build

FROM nginx:alpine
# Same-origin reverse-proxy of /ops -> the ops backend so the SPA needs no CORS.
ENV OPS_API_UPSTREAM=http://ops-backend:8100
COPY nginx.default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
