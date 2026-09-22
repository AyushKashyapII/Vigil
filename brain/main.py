from vigil_brain.parser.store import read_findings


def main() -> None:
    print("vigil brain starting")

    findings = read_findings()
    print(f"read {len(findings)} findings from collector's store")

    for f in findings:
        print(f"[{f.recorded_at}] {f.rule} {f.subject}: {f.detail}")


if __name__ == "__main__":
    main()
