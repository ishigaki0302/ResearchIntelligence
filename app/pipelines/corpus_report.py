"""Corpus Report Generation Pipeline.

Assembles MVP 3 deliverables into a self-contained HTML report:
  1. Topic Atlas (D3.js scatter plot + cluster list)
  2. Personalized Top30 papers (with explanations)
  3. Research Gaps Top10 (with experiment proposals)

Also supports --format markdown for a Markdown version.

Output directory: data/corpus/reports/report_<timestamp>/
  index.html          — self-contained HTML (inlines all JS/CSS)
  report.md           — Markdown version (optional)
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from app.core.config import get_config, resolve_path

logger = logging.getLogger(__name__)

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Corpus Analysis Report — {date}</title>
<style>
:root{{--bg:#fafafa;--fg:#1a1a1a;--card:#fff;--border:#e0e0e0;--accent:#2563eb;--accent-light:#dbeafe;--muted:#6b7280}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
background:var(--bg);color:var(--fg);line-height:1.6;padding:2rem}}
h1{{font-size:1.8rem;margin-bottom:.5rem}}
h2{{font-size:1.3rem;margin:2rem 0 .75rem;padding-bottom:.3rem;border-bottom:2px solid var(--border)}}
h3{{font-size:1rem;margin:.75rem 0 .25rem;color:var(--accent)}}
.meta{{color:var(--muted);font-size:.9rem;margin-bottom:2rem}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:1rem;margin-bottom:.75rem}}
.score{{font-size:.85rem;color:var(--muted)}}
.reason{{font-size:.88rem;color:#374151;margin-top:.3rem;font-style:italic}}
.keywords{{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.4rem}}
.kw{{background:var(--accent-light);color:var(--accent);padding:.15rem .5rem;border-radius:12px;font-size:.8rem}}
.experiment{{background:#f0fdf4;border-left:3px solid #059669;padding:.75rem;
margin-top:.5rem;font-size:.9rem;border-radius:0 4px 4px 0}}
.gap-tags{{font-size:.8rem;color:var(--muted)}}
canvas{{width:100%!important;max-height:480px}}
.section{{margin-bottom:3rem}}
</style>
</head>
<body>
<h1>Corpus Analysis Report</h1>
<p class="meta">Generated: {date} | Papers: {paper_count} | Clusters: {cluster_count}</p>

<!-- ① Topic Atlas -->
<div class="section">
<h2>① Topic Atlas（会議全体のトピック地図）</h2>
{cluster_section}
</div>

<!-- ② Personalized Top30 -->
<div class="section">
<h2>② あなたに近い論文 Top30</h2>
{top30_section}
</div>

<!-- ③ Research Gaps Top10 -->
<div class="section">
<h2>③ 研究の空白地帯 Top10（次の実験候補）</h2>
{gaps_section}
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script>
{chart_js}
</script>
</body>
</html>
"""

_CHART_JS = """\
(function(){{
  const clusters={clusters_json};
  const umap={umap_json};
  const colorFor=id=>`hsl(${{(id*53)%360}},65%,55%)`;
  const datasets=clusters.map(c=>{{
    return {{
      label:c.label_en||`Cluster ${{c.cluster_id}}`,
      backgroundColor:colorFor(c.cluster_id)+'99',
      borderColor:colorFor(c.cluster_id),
      borderWidth:1,pointRadius:4,
      data:c.paper_ids.filter(id=>umap[id]).map(id=>({{x:umap[id][0],y:umap[id][1]}}))
    }};
  }});
  const canvas=document.getElementById('atlasCanvas');
  if(canvas&&datasets.length){{
    new Chart(canvas,{{type:'scatter',data:{{datasets}},options:{{responsive:true,animation:false,
      plugins:{{legend:{{position:'right',labels:{{font:{{size:11}},boxWidth:12}}}}}},
      scales:{{x:{{display:false}},y:{{display:false}}}}
    }}}});
  }}
}})();
"""


def _corpus_dir() -> Path:
    cfg = get_config()
    d = resolve_path(cfg["storage"]["base_dir"]) / "corpus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return default


def _render_cluster_section(clusters: list, umap_data: dict) -> tuple[str, str]:
    """Return (html, chart_js_snippet)."""
    if not clusters:
        return (
            '<p style="color:var(--muted);">クラスタデータがありません。'
            "<code>ri corpus cluster</code> を実行してください。</p>",
            "",
        )

    chart_js = _CHART_JS.format(
        clusters_json=json.dumps(
            [
                {"cluster_id": c["cluster_id"], "label_en": c.get("label_en", ""), "paper_ids": c["paper_ids"]}
                for c in clusters
            ]
        ),
        umap_json=json.dumps(umap_data),
    )

    cluster_items = "".join(
        f'<div class="card">'
        f'<strong>{c.get("label_en") or "Cluster " + str(c["cluster_id"])}</strong>'
        f' <span class="score">({len(c["paper_ids"])} papers)</span>'
        f'<div class="keywords">'
        + "".join(f'<span class="kw">{kw}</span>' for kw in c.get("keywords", [])[:6])
        + "</div></div>"
        for c in clusters
    )

    html = f'<canvas id="atlasCanvas"></canvas>\n<div style="margin-top:1rem">{cluster_items}</div>'
    return html, chart_js


def _render_top30_section(top30: list) -> str:
    if not top30:
        return (
            '<p style="color:var(--muted);">Top30 データがありません。'
            "<code>ri corpus personalize &lt;profile&gt;</code> を実行してください。</p>"
        )
    items = "".join(
        f'<div class="card">'
        f'<h3>[{r["rank"]}] {r["title"]}</h3>'
        f'<p class="score">similarity: {r["score"]:.3f}</p>'
        + (f'<p class="reason">{r["reason"]}</p>' if r.get("reason") else "")
        + "</div>"
        for r in top30[:30]
    )
    return items


def _render_gaps_section(gaps: list) -> str:
    if not gaps:
        return (
            '<p style="color:var(--muted);">ギャップデータがありません。'
            "<code>ri corpus gaps</code> を実行してください。</p>"
        )
    items = "".join(
        f'<div class="card">'
        f'<h3>[{g["rank"]}] {g["description"]}</h3>'
        f'<p class="gap-tags">{g.get("tag1", "")} × {g.get("tag2", "")}</p>'
        + (f'<div class="experiment">{g["experiment"]}</div>' if g.get("experiment") else "")
        + "</div>"
        for g in gaps[:10]
    )
    return items


def generate_report(output_dir: Path | None = None, fmt: str = "html") -> Path:
    """Generate the corpus analysis report.

    Args:
        output_dir: Output directory. Default: data/corpus/reports/report_<timestamp>/
        fmt: "html" or "markdown".

    Returns: Path to the generated report file.
    """
    corpus_dir = _corpus_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if output_dir is None:
        output_dir = corpus_dir / "reports" / f"report_{timestamp}"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    clusters = _load_json(corpus_dir / "cluster_summary.json", [])
    umap_data = _load_json(corpus_dir / "umap2d.json", {})
    top30 = _load_json(corpus_dir / "personalized_top30.json", [])
    gaps = _load_json(corpus_dir / "gaps_top10.json", [])

    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    paper_count = sum(len(c.get("paper_ids", [])) for c in clusters) if clusters else len(top30)

    if fmt == "markdown":
        md = _render_markdown(date_str, clusters, top30, gaps)
        out_path = output_dir / "report.md"
        out_path.write_text(md, encoding="utf-8")
        logger.info(f"Markdown report written to {out_path}")
        return out_path

    # HTML
    cluster_html, chart_js = _render_cluster_section(clusters, umap_data)
    top30_html = _render_top30_section(top30)
    gaps_html = _render_gaps_section(gaps)

    html = _HTML_TEMPLATE.format(
        date=date_str,
        paper_count=paper_count,
        cluster_count=len(clusters),
        cluster_section=cluster_html,
        top30_section=top30_html,
        gaps_section=gaps_html,
        chart_js=chart_js,
    )

    out_path = output_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info(f"HTML report written to {out_path}")
    return out_path


def _render_markdown(date_str: str, clusters: list, top30: list, gaps: list) -> str:
    lines = [
        "# Corpus Analysis Report",
        "",
        f"Generated: {date_str}",
        "",
        "## ① Topic Atlas",
        "",
    ]
    for c in clusters:
        label = c.get("label_en") or f"Cluster {c['cluster_id']}"
        kws = ", ".join(c.get("keywords", [])[:5])
        lines.append(f"- **{label}** ({len(c['paper_ids'])} papers) — {kws}")

    lines += ["", "## ② あなたに近い論文 Top30", ""]
    for r in top30[:30]:
        lines.append(f"### [{r['rank']}] {r['title']} (score={r['score']:.3f})")
        if r.get("reason"):
            lines.append(f"> {r['reason']}")
        lines.append("")

    lines += ["## ③ 研究の空白地帯 Top10", ""]
    for g in gaps[:10]:
        lines.append(f"### [{g['rank']}] {g['description']}")
        lines.append(f"- Tags: `{g.get('tag1','')}` × `{g.get('tag2','')}`")
        if g.get("experiment"):
            lines.append(f"- 実験案: {g['experiment']}")
        lines.append("")

    return "\n".join(lines)
