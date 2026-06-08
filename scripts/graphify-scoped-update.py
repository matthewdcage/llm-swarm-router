#!/usr/bin/env python3
"""Rebuild graphify on scoped netllm source (excludes packaging/_build)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.export import to_json
from graphify.extract import extract
from graphify.report import generate
from graphify.wiki import to_wiki

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "graphify-out"
SKIP = {".venv", "_build", "_export", "dist", "node_modules", ".git", "graphify-out"}


def collect(rel: str, exts: set[str]) -> list[Path]:
    base = ROOT / rel
    found: list[Path] = []
    if base.is_file():
        return [base] if base.suffix.lower() in exts else []
    if not base.is_dir():
        return []
    for path in base.rglob("*"):
        if (
            path.is_file()
            and path.suffix.lower() in exts
            and not SKIP.intersection(path.parts)
        ):
            found.append(path)
    return sorted(found)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    (OUT / ".graphify_python").write_text(sys.executable)
    (OUT / ".graphify_root").write_text(str(ROOT))

    code_files = collect("packages", {".py"})
    code_files += collect("tests", {".py"})
    code_files += collect("packaging/linux", {".sh"})
    code_files += collect("packaging/windows", {".ps1"})
    code_files = sorted(set(code_files))

    print(f"AST extract: {len(code_files)} Python files")
    ast = extract(code_files, cache_root=ROOT)
    print(f"  nodes={len(ast['nodes'])} edges={len(ast['edges'])}")

    semantic_nodes = [
        {
            "id": "platform_matrix",
            "label": "Platform Install Matrix",
            "file_type": "document",
            "source_file": "docs/platform-matrix.md",
            "source_location": None,
            "source_url": None,
            "captured_at": None,
            "author": None,
            "contributor": None,
        },
        {
            "id": "web_dashboard",
            "label": "Local Web Dashboard /ui/",
            "file_type": "document",
            "source_file": "packages/netllm-agent/src/netllm_agent/static/index.html",
            "source_location": None,
            "source_url": None,
            "captured_at": None,
            "author": None,
            "contributor": None,
        },
    ]
    semantic_edges = [
        {
            "source": "web_dashboard",
            "target": "platform_matrix",
            "relation": "conceptually_related_to",
            "confidence": "INFERRED",
            "confidence_score": 0.9,
            "source_file": "docs/platform-matrix.md",
            "source_location": None,
            "weight": 1.0,
        },
    ]

    seen = {n["id"] for n in ast["nodes"]}
    merged_nodes = list(ast["nodes"])
    for node in semantic_nodes:
        if node["id"] not in seen:
            merged_nodes.append(node)
            seen.add(node["id"])

    merged = {
        "nodes": merged_nodes,
        "edges": ast["edges"] + semantic_edges,
        "hyperedges": [],
        "input_tokens": 0,
        "output_tokens": 0,
    }

    graph = build_from_json(merged)
    communities = cluster(graph)
    cohesion = score_all(graph, communities)
    gods = god_nodes(graph)
    surprises = surprising_connections(graph, communities)

    labels: dict[int, str] = {}
    for cid, members in communities.items():
        sample = " ".join(
            graph.nodes[n].get("label", n) for n in list(members)[:6]
        ).lower()
        if any(k in sample for k in ("fastapi", "proxy", "agent", "service")):
            labels[cid] = "Agent HTTP"
        elif any(k in sample for k in ("discover", "swarm", "mdns", "peer")):
            labels[cid] = "Discovery Swarm"
        elif any(k in sample for k in ("lifecycle", "systemd", "install")):
            labels[cid] = "CLI Lifecycle"
        elif any(k in sample for k in ("platform", "dashboard", "matrix")):
            labels[cid] = "Install Docs"
        else:
            labels[cid] = f"Community {cid}"

    detection = {
        "total_files": len(code_files) + 5,
        "total_words": 45000,
        "needs_graph": True,
        "warning": None,
        "files": {"code": [], "document": []},
    }
    questions = suggest_questions(graph, communities, labels)
    report = generate(
        graph,
        communities,
        cohesion,
        labels,
        gods,
        surprises,
        detection,
        {"input": 0, "output": 0},
        str(ROOT),
        suggested_questions=questions,
    )
    (OUT / "GRAPH_REPORT.md").write_text(report)
    (OUT / ".graphify_labels.json").write_text(
        json.dumps({str(k): v for k, v in labels.items()})
    )
    to_json(graph, communities, str(OUT / "graph.json"), force=True)
    to_wiki(
        graph,
        communities,
        str(OUT / "wiki"),
        community_labels=labels,
        cohesion=cohesion,
        god_nodes_data=gods,
    )

    index = OUT / "wiki" / "index.md"
    prefix = (
        "# netllm knowledge wiki\n\n"
        "Cross-platform install and UI: "
        "[docs/platform-matrix.md](../../docs/platform-matrix.md).\n\n"
        "Web dashboard: http://127.0.0.1:11400/ui/\n\n"
    )
    if index.exists():
        body = index.read_text()
        if "platform-matrix" not in body:
            index.write_text(prefix + body)

    print(
        f"graphify scoped update: {graph.number_of_nodes()} nodes, "
        f"{len(communities)} communities, wiki at {index}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
