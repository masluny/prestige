"""
core/graphview.py - Graphviz DOT generation and rendering (no UI).

Produces DOT text for the class hierarchy (whole or scoped) and renders it
with any of the six layout engines to PNG / SVG / PDF.

Colours are tuned for the Ayu Mirage dark theme.
"""

import os
import shutil
import subprocess
import tempfile

from . import ofn

# Layout engines shipped with Graphviz.
ENGINES = [
    ("dot",   "dot - layered hierarchy (best for taxonomies)"),
    ("sfdp",  "sfdp - scalable force-directed (best for large graphs)"),
    ("neato", "neato - spring model"),
    ("fdp",   "fdp - force-directed"),
    ("circo", "circo - circular layout"),
    ("twopi", "twopi - radial layout"),
]
ENGINE_NAMES = [n for n, _d in ENGINES]

# Dark (Ayu Mirage) palette used inside the rendered graph.
GRAPH_BG            = "#1f2430"
GRAPH_NODE_FILL     = "#272d3a"
GRAPH_NODE_BORDER   = "#5c6773"
GRAPH_NODE_FONT     = "#cccac2"
GRAPH_FOCUS_FILL    = "#ffcc66"
GRAPH_FOCUS_BORDER  = "#ffd580"
GRAPH_FOCUS_FONT    = "#1f2430"
GRAPH_EDGE          = "#6b7587"
GRAPH_RESTR         = "#73d0ff"
GRAPH_FILLER_FILL   = "#1f3547"
GRAPH_FILLER_BORDER = "#73d0ff"

# Two complete palettes selectable via the `bg` parameter.
THEMES = {
    "dark": {
        "bg":            GRAPH_BG,
        "node_fill":     GRAPH_NODE_FILL,
        "node_border":   GRAPH_NODE_BORDER,
        "node_font":     GRAPH_NODE_FONT,
        "focus_fill":    GRAPH_FOCUS_FILL,
        "focus_border":  GRAPH_FOCUS_BORDER,
        "focus_font":    GRAPH_FOCUS_FONT,
        "edge":          GRAPH_EDGE,
        "restr":         GRAPH_RESTR,
        "filler_fill":   GRAPH_FILLER_FILL,
        "filler_border": GRAPH_FILLER_BORDER,
    },
    "light": {
        "bg":            "#ffffff",
        "node_fill":     "#fdf6e3",
        "node_border":   "#c8a64a",
        "node_font":     "#2a2418",
        "focus_fill":    "#ffcc66",
        "focus_border":  "#c8930a",
        "focus_font":    "#1a1409",
        "edge":          "#888888",
        "restr":         "#1e6db5",
        "filler_fill":   "#e7f0fb",
        "filler_border": "#1e6db5",
    },
}


def check_graphviz():
    """Return the path to the `dot` binary, or None when Graphviz is absent."""
    return shutil.which("dot")


def _esc(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _scope(model, focus, up, down):
    """Classes within `up` ancestor levels and `down` descendant levels."""
    nodes = {focus}
    frontier = {focus}
    for _ in range(up):
        nxt = set()
        for c in frontier:
            for p in model.parents.get(c, []):
                if p not in nodes:
                    nxt.add(p)
        nodes |= nxt
        frontier = nxt
        if not frontier:
            break
    frontier = {focus}
    for _ in range(down):
        nxt = set()
        for c in frontier:
            for ch in model.children.get(c, []):
                if ch not in nodes:
                    nxt.add(ch)
        nodes |= nxt
        frontier = nxt
        if not frontier:
            break
    return nodes


def build_dot(model, focus=None, up=2, down=2, whole=False,
              show_restrictions=False, bg="dark"):
    """Build DOT text for the chosen scope. Returns (dot_text, node_count).

    `bg` selects the colour palette: "dark" (Ayu Mirage) or "light" (white)."""
    t = THEMES.get(bg) or THEMES["dark"]
    if whole or not focus:
        nodes = set(model.entities["Class"])
    else:
        nodes = _scope(model, focus, up, down)

    lines = [
        "digraph ontology {",
        "  rankdir=BT;",
        '  bgcolor="%s";' % t["bg"],
        '  node [shape=box, style="rounded,filled", fillcolor="%s", '
        'color="%s", fontcolor="%s", fontname="Helvetica", fontsize=10];'
        % (t["node_fill"], t["node_border"], t["node_font"]),
        '  edge [color="%s", arrowsize=0.7];' % t["edge"],
    ]

    for q in sorted(nodes):
        attrs = ['label="%s"' % _esc(ofn.local_name(q))]
        if q == focus and not whole:
            attrs += ['fillcolor="%s"' % t["focus_fill"],
                      'color="%s"' % t["focus_border"],
                      'fontcolor="%s"' % t["focus_font"],
                      'penwidth=2']
        lines.append('  "%s" [%s];' % (_esc(q), ", ".join(attrs)))

    for q in nodes:
        for parent in model.parents.get(q, []):
            if parent in nodes:
                lines.append('  "%s" -> "%s";' % (_esc(q), _esc(parent)))

    if show_restrictions:
        extra = {}
        for q in list(nodes):
            for prop, filler in model.restriction_targets(q):
                if filler not in nodes:
                    extra[filler] = True
                lines.append(
                    '  "%s" -> "%s" [style=dashed, color="%s", '
                    'fontcolor="%s", fontsize=8, label="%s", '
                    'arrowsize=0.6, constraint=false];'
                    % (_esc(q), _esc(filler), t["restr"],
                       t["restr"], _esc(ofn.local_name(prop))))
        for filler in extra:
            lines.append('  "%s" [label="%s", shape=ellipse, '
                          'fillcolor="%s", color="%s"];'
                          % (_esc(filler), _esc(ofn.local_name(filler)),
                             t["filler_fill"], t["filler_border"]))

    lines.append("}")
    return "\n".join(lines), len(nodes)


def render(dot_text, engine, fmt="svg", dpi=96):
    """Render DOT in memory. Returns the bytes of the chosen format."""
    if engine not in ENGINE_NAMES:
        raise ValueError("unknown engine %r" % engine)
    if not check_graphviz():
        raise RuntimeError(
            "Graphviz is not installed. Install with: brew install graphviz")
    with tempfile.NamedTemporaryFile("w", suffix=".dot", delete=False) as fh:
        dot_path = fh.name
        fh.write(dot_text)
    try:
        proc = subprocess.run(
            [engine, "-T" + fmt, "-Gdpi=%d" % dpi, dot_path],
            capture_output=True, timeout=180)
    finally:
        try:
            os.unlink(dot_path)
        except OSError:
            pass
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()
                           or "Graphviz failed.")
    return proc.stdout


def render_svg(model, focus=None, up=2, down=2, whole=False,
               show_restrictions=False, engine="dot", bg="dark"):
    """Convenience: build DOT and render to SVG bytes."""
    dot_text, _ = build_dot(model, focus=focus, up=up, down=down,
                            whole=whole,
                            show_restrictions=show_restrictions, bg=bg)
    return render(dot_text, engine, fmt="svg")


def render_graph(model, focus=None, up=2, down=2, whole=False,
                 show_restrictions=False, engine="dot",
                 fmt="svg", bg="dark", dpi=120):
    """Build DOT and render to any Graphviz output format (svg / png / pdf)."""
    dot_text, _ = build_dot(model, focus=focus, up=up, down=down,
                            whole=whole,
                            show_restrictions=show_restrictions, bg=bg)
    return render(dot_text, engine, fmt=fmt, dpi=dpi)
