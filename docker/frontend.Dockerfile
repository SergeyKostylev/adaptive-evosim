# Frontend: статика через nginx + проксі /api і /ws на backend (PROJECT_SPEC.md §12)
FROM nginx:alpine

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/ /usr/share/nginx/html/

EXPOSE 80
