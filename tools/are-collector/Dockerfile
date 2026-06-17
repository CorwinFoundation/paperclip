FROM golang:1.22 AS build
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -buildmode=pie -ldflags="-s -w" -o /out/are-collector ./cmd/are-collector

FROM scratch
USER 65534:65534
COPY --from=build /out/are-collector /are-collector
ENTRYPOINT ["/are-collector"]
