from vigil_brain import telemetry
from vigil_brain.actions.pr import build_pr, write_draft
from vigil_brain.actions.slack import send_alert
from vigil_brain.alerts import ALERTERS, is_fresh
from vigil_brain.fixes.index import suggest_missing_index_fix, suggest_unused_index_fix
from vigil_brain.fixes.nplusone import suggest_n_plus_one_fix
from vigil_brain.fixes.rewrite import suggest_nested_subquery_rewrite
from vigil_brain.parser.store import latest_by_subject, read_findings
from vigil_brain.sandbox.verify import verify_fix
from vigil_brain.sandbox.verify_nplusone import verify_n_plus_one
from vigil_brain.sandbox.verify_rewrite import substitute_placeholders, verify_rewrite
from vigil_brain.telemetry import (
    VERDICT_HELPED,
    VERDICT_NOT_HELPED,
    VERDICT_NOT_VERIFIABLE,
    VERDICT_REJECTED,
    VERDICT_UNAVAILABLE,
    finding_span,
    llm_rewrites_rejected,
    record_verdict,
)

SUGGESTERS = [suggest_unused_index_fix, suggest_missing_index_fix]


def _verdict(helped: bool) -> str:
    return VERDICT_HELPED if helped else VERDICT_NOT_HELPED


def main() -> None:
    print("vigil brain starting")

    findings = read_findings()
    print(f"read {len(findings)} findings from collector's store")

    latest = latest_by_subject(findings)
    print(f"{len(latest)} distinct (rule, subject) findings after dedup")

    for f in latest:
        if not is_fresh(f):
            continue
        for check in ALERTERS:
            alert = check(f)
            if alert is None:
                continue
            print(f"ALERT [{alert.rule}]: {alert.message}")
            send_alert(alert)

    for f in latest:
        for suggest in SUGGESTERS:
            try:
                suggestion = suggest(f)
                if suggestion is None:
                    continue
                print(f"FIX SUGGESTION [{suggestion.rule}]: {suggestion.description}")
                print(f"  {suggestion.sql}")

                # Span opens only once there's a suggestion: most
                # (finding, suggester) pairs don't match at all, and an
                # empty trace per non-match would be pure noise.
                with finding_span(f, **{"vigil.fix.sql": suggestion.sql}) as span:
                    result = verify_fix(f, suggestion)
                    if result is None:
                        record_verdict(span, f.rule, VERDICT_NOT_VERIFIABLE)
                        print("  (not verifiable in the sandbox yet)")
                        continue
                    record_verdict(span, f.rule, _verdict(result.helped), result.before_ms, result.after_ms)
                    print(
                        f"  VERIFIED: {result.before_ms:.2f}ms -> {result.after_ms:.2f}ms "
                        f"({result.improvement_pct:.0f}% {'faster' if result.improvement_pct >= 0 else 'slower'}), "
                        f"helped={result.helped}"
                    )

                    draft = build_pr(f, suggestion, result)
                    if draft is not None:
                        out_dir = write_draft(draft)
                        span.set_attribute("vigil.pr_draft.path", str(out_dir))
                        print(f"  PR DRAFT written to {out_dir} (dry run, not opened)")
            except Exception as e:
                print(f"  SKIPPED finding {f.id} ({f.rule} {f.subject}): {e}")

    for f in latest:
        if f.rule != "nested_subquery":
            continue
        try:
            with finding_span(f) as span:
                literal_query = substitute_placeholders(f.query)
                rewritten = suggest_nested_subquery_rewrite(f, literal_query)
                if rewritten is None:
                    record_verdict(span, f.rule, VERDICT_UNAVAILABLE)
                    print(f"REWRITE [nested_subquery] finding={f.id}: (LLM rewrite unavailable)")
                    continue

                print(f"REWRITE SUGGESTION [nested_subquery] finding={f.id}:")
                print(f"  original:  {literal_query}")
                print(f"  rewritten: {rewritten}")

                result = verify_rewrite(f, rewritten)
                if result is None:
                    record_verdict(span, f.rule, VERDICT_NOT_VERIFIABLE)
                    print("  (not verifiable in the sandbox yet)")
                    continue
                if not result.results_match:
                    record_verdict(span, f.rule, VERDICT_REJECTED)
                    span.set_attribute("vigil.reject_reason", result.reject_reason or "unknown")
                    llm_rewrites_rejected.add(1, {"vigil.reject_reason": result.reject_reason or "unknown"})
                    print("  REJECTED: rewrite does not return the same results as the original")
                    continue

                record_verdict(span, f.rule, _verdict(result.helped), result.before_ms, result.after_ms)
                print(
                    f"  VERIFIED: {result.before_ms:.2f}ms -> {result.after_ms:.2f}ms, "
                    f"helped={result.helped}"
                )
                if result.helped:
                    print("  (dry run -- application-code PR generation not built yet, see ROADMAP.md)")
        except Exception as e:
            print(f"  SKIPPED finding {f.id} ({f.rule}): {e}")

    for f in latest:
        if f.rule != "possible_n_plus_one":
            continue
        try:
            with finding_span(f) as span:
                suggestion = suggest_n_plus_one_fix(f)
                if suggestion is None:
                    record_verdict(span, f.rule, VERDICT_NOT_VERIFIABLE)
                    print(f"N+1 [possible_n_plus_one] finding={f.id}: (query shape not handled)")
                    continue

                print(f"N+1 FIX SUGGESTION [{suggestion.rule}] finding={f.id}: {suggestion.description}")

                result = verify_n_plus_one(f)
                if result is None:
                    record_verdict(span, f.rule, VERDICT_NOT_VERIFIABLE)
                    print("  (not verifiable in the sandbox yet)")
                    continue

                record_verdict(span, f.rule, _verdict(result.helped), result.before_ms, result.after_ms)
                print(
                    f"  VERIFIED: {result.before_ms:.2f}ms (N sequential calls) -> "
                    f"{result.after_ms:.2f}ms (batched) "
                    f"({result.improvement_pct:.0f}% {'faster' if result.improvement_pct >= 0 else 'slower'}), "
                    f"helped={result.helped}"
                )
                if result.helped:
                    print("  (dry run -- application-code PR generation not built yet, see ROADMAP.md)")
        except Exception as e:
            print(f"  SKIPPED finding {f.id} ({f.rule}): {e}")


if __name__ == "__main__":
    shutdown_telemetry = telemetry.setup()
    try:
        main()
    finally:
        # Run-once process: without this, buffered spans/metrics -- i.e.
        # nearly all of them -- are dropped on exit. `finally` so a crash
        # still exports the spans that explain the crash.
        shutdown_telemetry()
