# MinIO той же сборки, что работает на проде, — из исходников.
#
# С осени 2025 MinIO не публикует бесплатные образы: minio/minio на Docker Hub
# удалён, quay.io закрыт. На сервере образ скачан раньше и работает; этот файл
# нужен тестовому стенду CI (чистая машина GitHub скачать образ уже не может)
# и на случай, если образ на сервере потеряется (docs/16-ci-cd.md, разд. 6).
#
# Замена официального образа один к одному: тот же выпуск minio и та же версия
# mc внутри (ими пользуются проверка здоровья `mc ready local` и
# deploy/backup.sh, deploy/restore.sh).
#   docker build -t astra-minio deploy/minio
#   docker run --rm --entrypoint minio astra-minio --version   # RELEASE.2025-09-07T16-13-09Z

ARG GO_VERSION=1.24

FROM golang:${GO_VERSION}-alpine AS build
RUN apk add --no-cache git
# На машине с малым объёмом памяти: --build-arg GOFLAGS=-p=1 (сборка медленнее).
ARG GOFLAGS=""
ENV GOFLAGS=$GOFLAGS
ARG MINIO_RELEASE_TAG=RELEASE.2025-09-07T16-13-09Z
ARG MC_RELEASE_TAG=RELEASE.2025-08-13T08-35-41Z
RUN git clone --quiet --depth 1 --branch "$MINIO_RELEASE_TAG" https://github.com/minio/minio /src/minio \
    && git clone --quiet --depth 1 --branch "$MC_RELEASE_TAG" https://github.com/minio/mc /src/mc
# Флаги как в их Makefile; MINIO_RELEASE/MC_RELEASE дают строку версии
# «RELEASE.…», как у официальной сборки, а не «DEVELOPMENT.…».
WORKDIR /src/minio
RUN CGO_ENABLED=0 go build -tags kqueue -trimpath \
        -ldflags "$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go)" -o /out/minio .
WORKDIR /src/mc
RUN CGO_ENABLED=0 go build -tags kqueue -trimpath \
        -ldflags "$(MC_RELEASE=RELEASE go run buildscripts/gen-ldflags.go)" -o /out/mc .

FROM alpine:3.22
RUN apk add --no-cache ca-certificates curl
COPY --from=build /out/minio /out/mc /usr/bin/
EXPOSE 9000 9001
# Как у официального образа: команда в compose — «server /data …».
ENTRYPOINT ["minio"]
CMD ["server", "/data"]
