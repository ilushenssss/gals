# Сборка SPA и раздача её nginx'ом. Тот же принцип, что у образа бэкенда:
# многостадийность, чтобы в рантайме не оставалось ни node, ни node_modules.
FROM node:22-alpine AS build
WORKDIR /app

# Слой зависимостей отдельно от исходников: правка кода не пересобирает npm ci.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig*.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src

# Подложка карты задаётся на сборке: VITE_* попадают в бандл, а не в рантайм.
# Пусто — работаем без тайлов (закрытый контур), см. MapProvider.
ARG VITE_TILE_URL=""
ENV VITE_TILE_URL=$VITE_TILE_URL
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

# Образ nginx работает от root только ради мастер-процесса; воркеры уже
# сбрасывают привилегии на nginx. Порт 80 внутри, наружу пробрасывает compose.
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
