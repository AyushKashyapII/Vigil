"""Brain's own traces and metrics, exported over OTLP (to SigNoz in this
repo's docker-compose). Mirrors collector/internal/telemetry.

Every finding brain acts on becomes its own trace, rooted at a
`verify_fix` span:

    verify_fix  (vigil.rule, vigil.finding.id, vigil.verdict, ...)
    ├── llm.chat            (rewrite path only: model, tokens)
    ├── sandbox.clone       (createdb + pg_dump | psql)
    ├── rewrite.diff        (rewrite path only: EXCEPT-based equivalence)
    ├── benchmark.before    (vigil.benchmark.ms)
    ├── fix.apply
    ├── benchmark.after     (vigil.benchmark.ms)
    └── sandbox.drop

One trace per finding (not one per brain run) because a finding is the
unit someone wants to look up -- and in Phase 3 it's what the collector's
span links to.

All config comes from the standard OTEL_* env vars; nothing here names
SigNoz.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Callable

from opentelemetry import metrics, trace
from opentelemetry.trace import Link, Span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# Attribute keys -- same names the Go collector uses, so one SigNoz query
# can group both services' telemetry by rule.
ATTR_RULE = "vigil.rule"
ATTR_VERDICT = "vigil.verdict"

# vigil.verdict values. A closed set on purpose: it's a METRIC attribute,
# so every distinct value is a new time series.
VERDICT_HELPED = "helped"              # verified and >= 20% faster
VERDICT_NOT_HELPED = "not_helped"      # verified, but not faster enough
VERDICT_REJECTED = "rejected"          # LLM rewrite returned different rows
VERDICT_NOT_VERIFIABLE = "not_verifiable"  # no benchmark for this shape yet
VERDICT_UNAVAILABLE = "unavailable"    # LLM gave nothing usable
VERDICT_ERROR = "error"                # exception during verification

_SCOPE = "vigil_brain"

# Explicitly W3C trace context, not the globally configured propagator:
# the format is fixed by what the Go collector writes into SQLite, not by
# whatever this process happens to be configured with.
_traceparent_format = TraceContextTextMapPropagator()

tracer = trace.get_tracer(_SCOPE)
_meter = metrics.get_meter(_SCOPE)

# Instruments are created at import time, before setup() runs. That's
# fine: the API hands out proxy objects that start forwarding to the real
# MeterProvider once one is installed (and are no-ops if none ever is).
fixes_verified = _meter.create_counter(
    "vigil.fixes.verified",
    unit="{fix}",
    description="Fix attempts by outcome (vigil.rule, vigil.verdict).",
)
llm_rewrites_rejected = _meter.create_counter(
    "vigil.llm.rewrites.rejected",
    unit="{rewrite}",
    description="LLM-proposed rewrites that failed the EXCEPT equivalence check, by reason.",
)
fix_latency_ratio = _meter.create_histogram(
    "vigil.fix.latency_ratio",
    unit="1",
    description=(
        "after_ms / before_ms for each verified fix. 1.0 = no change, "
        "0.04 = 96% faster, >1 = slower. <= 0.8 is the 'helped' threshold."
    ),
    # Ratio, not percent-improvement: always >= 0 (a histogram of values
    # that can go negative, like "-30% improvement", is awkward), and the
    # buckets can put an edge exactly on the 0.8 acceptance threshold.
    explicit_bucket_boundaries_advisory=[0.01, 0.05, 0.1, 0.25, 0.5, 0.8, 1.0, 1.25, 2.0],
)


def setup() -> Callable[[], None]:
    """Installs OTLP-exporting trace and meter providers. Returns a
    shutdown function that MUST be called before the process exits.

    Brain is a run-once batch job, not a long-lived service. Spans are
    exported by a background BatchSpanProcessor and metrics by a periodic
    reader -- if the process just exits, whatever's still buffered (which,
    for a short run, is everything) is silently lost. shutdown() flushes
    both.

    Best-effort: if the SDK isn't installed or setup fails, brain still
    runs and every span/metric call is a no-op.
    """
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        return lambda: None
    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Resource.create() already reads OTEL_SERVICE_NAME and
        # OTEL_RESOURCE_ATTRIBUTES -- but attributes passed here would
        # OVERRIDE them, so the default name is only passed if the env
        # var isn't set.
        attrs = {} if os.environ.get("OTEL_SERVICE_NAME") else {"service.name": "vigil-brain"}
        resource = Resource.create(attrs)

        tp = TracerProvider(resource=resource)
        tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        mp = MeterProvider(
            resource=resource,
            metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
        )
        trace.set_tracer_provider(tp)
        metrics.set_meter_provider(mp)
    except Exception as e:  # never let telemetry stop brain doing its job
        print(f"telemetry disabled: {e}")
        return lambda: None

    def shutdown() -> None:
        # Order matters a little: spans first (the bigger buffer), then
        # metrics (MeterProvider.shutdown does one final collect+export).
        tp.shutdown()
        mp.shutdown()

    return shutdown


def detection_link(finding) -> list[Link]:
    """A span LINK to the collector span that detected `finding`, parsed
    from the traceparent the collector stored alongside it.

    A link, not a parent. Parent/child means "this work is PART OF that
    operation" -- the caller waits for it, and it falls within the
    parent's lifetime. Neither is true here: the collector's poll cycle
    ended long ago (maybe hours), never waited for brain, and brain may
    act on findings from many different cycles in one run. Making it a
    child would stretch a 200ms poll trace across hours and graft brain's
    work into a trace it isn't part of. A link says "caused by / related
    to" without either claim -- the standard pattern for anything that
    goes through a queue, which is exactly what the findings table is.
    """
    if not finding.traceparent:
        return []
    ctx = _traceparent_format.extract({"traceparent": finding.traceparent})
    detected = trace.get_current_span(ctx).get_span_context()
    if not detected.is_valid:  # malformed string -> INVALID_SPAN_CONTEXT
        return []
    return [Link(detected, attributes={"vigil.link.type": "detected_by"})]


@contextmanager
def finding_span(finding, **extra: str) -> Iterator[Span]:
    """Root span for everything brain does with one finding, linked back
    to the collector span that detected it (see detection_link).

    Exceptions propagate (callers already catch + print them), but are
    recorded on the span first and counted as verdict=error.
    start_as_current_span itself records the exception and sets ERROR
    status as it propagates through.
    """
    with tracer.start_as_current_span(
        "verify_fix",
        # A NEW trace per finding, even if something else is current.
        context=trace.set_span_in_context(trace.INVALID_SPAN),
        # Links must be given at span start (samplers may use them).
        links=detection_link(finding),
        attributes={
            ATTR_RULE: finding.rule,
            "vigil.finding.id": finding.id,
            "vigil.finding.recorded_at": finding.recorded_at,
            # Fine on a span (one record), never on a metric (unbounded).
            "vigil.subject": finding.subject,
            **extra,
        },
    ) as span:
        try:
            yield span
        except Exception:
            _count_verdict(finding.rule, VERDICT_ERROR)
            span.set_attribute(ATTR_VERDICT, VERDICT_ERROR)
            raise


def record_verdict(
    span: Span,
    rule: str,
    verdict: str,
    before_ms: float | None = None,
    after_ms: float | None = None,
) -> None:
    """Puts the outcome on the finding's root span -- as attributes, so
    traces can be FILTERED by it in SigNoz (e.g. vigil.verdict=rejected),
    which a separate zero-length "verdict" child span couldn't be -- and
    records the matching metrics.
    """
    span.set_attribute(ATTR_VERDICT, verdict)
    _count_verdict(rule, verdict)

    if before_ms is None or after_ms is None:
        return
    span.set_attribute("vigil.before_ms", before_ms)
    span.set_attribute("vigil.after_ms", after_ms)
    if before_ms > 0:
        ratio = after_ms / before_ms
        span.set_attribute("vigil.latency_ratio", ratio)
        span.set_attribute("vigil.improvement_pct", (1 - ratio) * 100)
        fix_latency_ratio.record(ratio, {ATTR_RULE: rule})


def _count_verdict(rule: str, verdict: str) -> None:
    fixes_verified.add(1, {ATTR_RULE: rule, ATTR_VERDICT: verdict})
