from vigil_brain.fixes.index import suggest_unused_index_fix
from vigil_brain.parser.store import latest_by_subject, read_findings


def main() -> None:
    print("vigil brain starting")

    findings = read_findings()
    print(f"read {len(findings)} findings from collector's store")

    latest = latest_by_subject(findings)
    print(f"{len(latest)} distinct (rule, subject) findings after dedup")

    for f in latest:
        suggestion = suggest_unused_index_fix(f)
        if suggestion is None:
            continue
        print(f"FIX SUGGESTION [{suggestion.rule}]: {suggestion.description}")
        print(f"  {suggestion.sql}")


if __name__ == "__main__":
    main()
