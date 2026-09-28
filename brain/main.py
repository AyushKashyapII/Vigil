from vigil_brain.alerts import ALERTERS, is_fresh
from vigil_brain.fixes.index import suggest_missing_index_fix, suggest_unused_index_fix
from vigil_brain.parser.store import latest_by_subject, read_findings
from vigil_brain.sandbox.verify import verify_fix

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
            except Exception as e:
                print(f"  SKIPPED finding {f.id} ({f.rule} {f.subject}): {e}")


if __name__ == "__main__":
    main()
