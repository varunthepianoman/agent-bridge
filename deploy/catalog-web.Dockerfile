FROM node:22-alpine AS build
WORKDIR /app
COPY catalog-web/package.json catalog-web/package-lock.json ./
RUN npm ci
COPY schemas /schemas
COPY catalog-web ./
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
