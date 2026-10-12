"""Run every offline test suite (free, no Claude calls) and print one summary line each.

Run from the project folder:  .venv/bin/python tests/run_all.py
Add -v to see every PASS/FAIL line:  .venv/bin/python tests/run_all.py -v
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = ["test_tools.py", "test_mcp_server.py", "test_hooks.py", "test_ledger.py", "test_writer.py"]

failed = 0
for name in SUITES:
    if not (HERE / name).exists():
        continue
    run = subprocess.run([sys.executable, str(HERE / name)], capture_output=True, text=True, cwd=HERE.parent)
    lines = run.stdout.splitlines()
    passes = sum(1 for line in lines if line.strip().startswith("PASS"))
    fails = [line for line in lines if line.strip().startswith("FAIL")]
    ok = run.returncode == 0
    failed += not ok
    print(f"{'OK  ' if ok else 'FAIL'}  {name:<22} {passes} passed, {len(fails)} failed")
    if "-v" in sys.argv or not ok:
        print("\n".join("      " + line for line in lines if line.strip()))
        if run.stderr.strip() and not ok:
            print("      stderr:", run.stderr.strip()[-500:])
print(f"\n{'All suites passed.' if not failed else f'{failed} suite(s) failed.'}")
sys.exit(1 if failed else 0)
