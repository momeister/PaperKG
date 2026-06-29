# Frontend image: build the React/Vite app, then serve the static bundle with nginx.

# ---- Build stage ----
FROM node:20-alpine AS build
WORKDIR /app

# The browser talks to the backend directly at this URL (read in src/api.ts as
# import.meta.env.VITE_API_BASE_URL). It must be reachable from the user's browser,
# i.e. the host-published backend address — NOT a docker-internal hostname.
ARG VITE_API_BASE_URL=http://localhost:8000

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# Vite (mode=production) reads .env.production at build time; bake the API URL in.
RUN echo "VITE_API_BASE_URL=$VITE_API_BASE_URL" > .env.production \
    && npm run build

# ---- Runtime stage ----
FROM nginx:alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
