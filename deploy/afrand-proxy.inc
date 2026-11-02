client_max_body_size 2m;
location / {
    proxy_pass http://127.0.0.1:5057;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 150s;
    proxy_connect_timeout 10s;
}
