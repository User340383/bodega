# Stage "dev": Vite dev server, used by docker-compose.yml and compose.test.yml (lint, tests, build).
FROM node:22-alpine AS dev

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]


# Stage "build": the static production bundle.
FROM dev AS build
RUN npm run build


# Stage "prod" (default, used by Render): nginx serves the SPA (M3 4.1, 8.1).
# /config.js is written at container start from API_BASE_URL, so one image works in every environment.
FROM nginx:1.27-alpine AS prod

ENV PORT=8080 \
    API_BASE_URL=""

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.d/40-bodega-config.sh /docker-entrypoint.d/40-bodega-config.sh
COPY --from=build /app/dist /usr/share/nginx/html
RUN chmod +x /docker-entrypoint.d/40-bodega-config.sh \
    && rm -f /etc/nginx/conf.d/default.conf

EXPOSE 8080
# nginx's own entrypoint renders the template with $PORT and runs 40-bodega-config.sh before starting.
