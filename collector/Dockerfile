FROM golang:1.26 AS build

WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 go build -o /vigil-collector ./cmd/vigil-collector

FROM gcr.io/distroless/static-debian12

WORKDIR /data

COPY --from=build /vigil-collector /vigil-collector

ENTRYPOINT ["/vigil-collector"]
