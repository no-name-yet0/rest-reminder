"""一键验证：编译检查 + 自检 + 端到端测试。

    python tools/verify.py

先做编译检查是有原因的：脚本里只要有一个缩进错误，
后面两步都会静默失败（进程根本起不来，报告还是上一次的旧文件），
很容易误判成"测试通过了"。
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def run(label: str, args: list) -> int:
    print("=" * 60)
    print("[{}] {}".format(label, " ".join(os.path.basename(a) for a in args[1:])))
    proc = subprocess.run(args, cwd=ROOT)
    print("[{}] 退出码 {}".format(label, proc.returncode))
    return proc.returncode


def tail_report(name: str, lines: int = 3) -> str:
    path = os.path.join(ROOT, name)
    if not os.path.isfile(path):
        return "  (没有生成报告: {})".format(name)
    with open(path, "r", encoding="utf-8") as fh:
        content = [line for line in fh.read().splitlines() if line.strip()]
    return "\n".join("  " + line for line in content[-lines:])


def main() -> int:
    results = []

    compile_rc = run(
        "编译检查",
        [PY, "-m", "compileall", "-q", os.path.join(ROOT, "src"), os.path.join(ROOT, "tools")],
    )
    results.append(("编译检查", compile_rc))

    if compile_rc != 0:
        print("编译不通过，后面两步无法可信执行，直接停止。")
        return 1

    selftest_rc = run("自检", [PY, os.path.join(ROOT, "tools", "selftest.py")])
    results.append(("自检", selftest_rc))

    e2e_rc = run("端到端", [PY, os.path.join(ROOT, "tools", "e2e_test.py")])
    results.append(("端到端", e2e_rc))

    print("=" * 60)
    print("汇总")
    for label, code in results:
        print("  {:<8} {}".format(label, "通过" if code == 0 else "失败 (exit={})".format(code)))
    print()
    print("自检报告尾部:")
    print(tail_report("_selftest.txt"))
    print("端到端报告尾部:")
    print(tail_report("_e2e.txt"))

    return 0 if all(code == 0 for _, code in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
