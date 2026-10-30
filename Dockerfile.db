FROM postgres:15
COPY app/schema.sql /docker-entrypoint-initdb.d/01-schema.sql
COPY seed/seed.sql /docker-entrypoint-initdb.d/02-seed.sql
