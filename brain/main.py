from vigil_brain.actions.pr import build_pr, write_draft
from vigil_brain.actions.slack import send_alert
from vigil_brain.alerts import ALERTERS, is_fresh
from vigil_brain.fixes.index import suggest_missing_index_fix, suggest_unused_index_fix
from vigil_brain.fixes.rewrite import suggest_nested_subquery_rewrite
from vigil_brain.parser.store import latest_by_subject, read_findings
from vigil_brain.sandbox.verify import verify_fix
from vigil_brain.sandbox.verify_rewrite import substitute_placeholders, verify_rewrite

SUGGESTERS = [suggest_unused_index_fix, suggest_missing_index_fix]


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

                result = verify_fix(f, suggestion)
                if result is None:
                    print("  (not verifiable in the sandbox yet)")
                    continue
                print(
                    f"  VERIFIED: {result.before_ms:.2f}ms -> {result.after_ms:.2f}ms "
                    f"({result.improvement_pct:.0f}% {'faster' if result.improvement_pct >= 0 else 'slower'}), "
                    f"helped={result.helped}"
                )

                draft = build_pr(f, suggestion, result)
                if draft is not None:
                    out_dir = write_draft(draft)
                    print(f"  PR DRAFT written to {out_dir} (dry run, not opened)")
            except Exception as e:
                print(f"  SKIPPED finding {f.id} ({f.rule} {f.subject}): {e}")

    for f in latest:
        if f.rule != "nested_subquery":
            continue
        try:
            literal_query = substitute_placeholders(f.query)
            rewritten = suggest_nested_subquery_rewrite(f, literal_query)
            if rewritten is None:
                print(f"REWRITE [nested_subquery] finding={f.id}: (LLM rewrite unavailable)")
                continue

            print(f"REWRITE SUGGESTION [nested_subquery] finding={f.id}:")
            print(f"  original:  {literal_query}")
            print(f"  rewritten: {rewritten}")

            result = verify_rewrite(f, rewritten)
            if result is None:
                print("  (not verifiable in the sandbox yet)")
                continue
            if not result.results_match:
                print("  REJECTED: rewrite does not return the same results as the original")
                continue

            print(
                f"  VERIFIED: {result.before_ms:.2f}ms -> {result.after_ms:.2f}ms, "
                f"helped={result.helped}"
            )
            if result.helped:
                print("  (dry run -- application-code PR generation not built yet, see ROADMAP.md)")
        except Exception as e:
            print(f"  SKIPPED finding {f.id} ({f.rule}): {e}")


if __name__ == "__main__":
    main()
