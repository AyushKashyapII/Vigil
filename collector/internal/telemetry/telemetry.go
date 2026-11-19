// Package telemetry wires the collector's own traces and metrics to an
// OTLP endpoint (SigNoz, in this repo's docker-compose). It's how Vigil
// observes *itself* -- separate from internal/metrics, which is about the
// Postgres stats Vigil observes.
package telemetry

import (
	"context"
	"errors"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetricgrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

// ScopeName identifies this code as the source of its spans/metrics (the
// "instrumentation scope"), distinct from service.name, which identifies
// the process.
const ScopeName = "github.com/ayushkashyap/vigil/collector"

// Setup installs global trace and meter providers that export over OTLP
// gRPC, and returns a shutdown func that flushes anything still buffered.
//
// All endpoint/protocol config comes from the standard OTEL_* env vars
// (OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME, OTEL_RESOURCE_ATTRIBUTES,
// OTEL_METRIC_EXPORT_INTERVAL, ...) -- the exporters and resource detector
// read them directly, so nothing here is hardcoded to SigNoz.
//
// Neither exporter connects eagerly: an unreachable collector doesn't fail
// Setup, it shows up later as export errors (logged by the SDK's global
// error handler) while the collector itself keeps working.
func Setup(ctx context.Context) (shutdown func(context.Context) error, err error) {
	res, err := resource.New(ctx,
		// Default first: WithFromEnv comes after, so OTEL_SERVICE_NAME
		// overrides it (later options win when resources are merged).
		resource.WithAttributes(attribute.String("service.name", "vigil-collector")),
		resource.WithFromEnv(),
		resource.WithTelemetrySDK(),
		resource.WithHost(),
	)
	if err != nil {
		return nil, err
	}

	traceExp, err := otlptracegrpc.New(ctx)
	if err != nil {
		return nil, err
	}
	// WithBatcher, not WithSyncer: spans are queued and exported in the
	// background, so a slow/down SigNoz never adds latency to a poll.
	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(traceExp),
		sdktrace.WithResource(res),
	)

	metricExp, err := otlpmetricgrpc.New(ctx)
	if err != nil {
		_ = tp.Shutdown(ctx)
		return nil, err
	}
	mp := sdkmetric.NewMeterProvider(
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(metricExp)),
		sdkmetric.WithResource(res),
	)

	otel.SetTracerProvider(tp)
	otel.SetMeterProvider(mp)
	// W3C traceparent -- unused until Phase 3 serialises a finding's span
	// context into SQLite for brain to link to.
	otel.SetTextMapPropagator(propagation.TraceContext{})

	return func(ctx context.Context) error {
		return errors.Join(tp.Shutdown(ctx), mp.Shutdown(ctx))
	}, nil
}
