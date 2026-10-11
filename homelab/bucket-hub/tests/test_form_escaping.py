from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

HUB = Path(__file__).resolve().parents[1]
FORM = HUB / "form.html"
JS_TEST = HUB / "tests" / "js" / "hub-escape.test.mjs"

# Known-safe interpolations (numeric widths, counts, static class names).
SAFE_EXPR = (
    "w",
    "pct",
    "doneN",
    "errorN",
    "totalN",
    "dot",
    "st",
    "stColor",
    "dur",
    "timeout",
    "jobHead",
    "topJobs",
    "cancelBtn",
    "retry",
    "approve",
    "by",
    "urlLine",
    "err",
    "errHtml",
    "tags",
    "line",
    "bar",
    "dragHandle",
    "on",
    "running",
    "draggable",
    "running ? ' is-running' : ''",
    "j.status === 'queued' ? ' data-queue-draggable=\"1\"' : ''",
    "queueJobActionButtons(j)",
)


def _extract_fn(src: str, name: str) -> str:
    m = re.search(rf"function {name}\s*\(", src)
    assert m, f"missing {name}"
    start = m.start()
    i = src.find("{", m.end())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start : j + 1]
    raise AssertionError(f"unbalanced {name}")


def _inner_html_chunks(fn_src: str) -> list[str]:
    chunks = []
    for m in re.finditer(
        r"(?:innerHTML|insertAdjacentHTML)\s*=\s*`([\s\S]*?)`|"
        r"insertAdjacentHTML\([^,]+,\s*`([\s\S]*?)`",
        fn_src,
    ):
        chunks.append(m.group(1) or m.group(2))
    # also return statements that are template HTML
    for m in re.finditer(r"return\s+`([\s\S]*?)`;", fn_src):
        if "<" in m.group(1):
            chunks.append(m.group(1))
    return chunks


def _interpolations(chunk: str) -> list[str]:
    return re.findall(r"\$\{([^}]+)\}", chunk)


@pytest.mark.parametrize("fn", ["loadNightSummary", "renderJobRow", "renderQueueRow"])
def test_innerhtml_interpolations_escaped(fn):
    src = FORM.read_text(encoding="utf-8")
    body = _extract_fn(src, fn)
    offenders = []
    for chunk in _inner_html_chunks(body):
        for expr in _interpolations(chunk):
            text = expr.strip()
            if text.startswith("escHtml(") or text.startswith("escAttr("):
                continue
            if text.startswith("safeHref("):
                continue
            if text in SAFE_EXPR:
                continue
            if "is-running" in text or "data-queue-draggable" in text:
                continue
            if text.startswith("queueJobActionButtons("):
                continue
            if re.fullmatch(r"[\d.]+", text):
                continue
            offenders.append((fn, text))
    assert not offenders, f"unescaped interpolations: {offenders}"


def test_jackett_key_redacted_for_display():
    js = (HUB / "static" / "hub-escape.js").read_text(encoding="utf-8")
    assert "redactSecrets" in js
    # Exercise via node when present; otherwise a regex stand-in matching the helper.
    url = "http://127.0.0.1:9117/dl/x?jackett_apikey=abc123&foo=1"
    if shutil.which("node"):
        code = (
            "const {redactSecrets}=require('./static/hub-escape.js');"
            "const u=redactSecrets(process.argv[1]);"
            "if(!u.includes('apikey=***')||u.includes('abc123')) process.exit(1);"
        )
        r = subprocess.run(
            ["node", "-e", code, url],
            cwd=HUB,
            check=False,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
    else:
        out = re.sub(r"([?&](?:jackett_)?apikey=)[^&]*", r"\1***", url, flags=re.I)
        assert "apikey=***" in out
        assert "abc123" not in out


def test_node_escape_helpers():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    r = subprocess.run(
        ["node", "--test", str(JS_TEST)],
        cwd=HUB,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
