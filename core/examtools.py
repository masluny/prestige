"""
core/examtools.py - analysis tools (no UI).

  * run_query        - competency-question lookups
  * pitfall_scan     - OOPS!-style quality checks
  * metrics          - report counts, depth, annotation coverage
  * hierarchy_outline- indented taxonomy text
  * glossary         - text / markdown / csv class dictionary
  * diff_files       - axiom-level diff against another file
"""

import csv
import io
from collections import Counter, defaultdict

from . import ofn
from .ofn import Iri, Node


# ---------------------------------------------------------------------------
# Query catalogue
# ---------------------------------------------------------------------------

QUERY_TYPES = [
    # key, label, (needs_class, needs_property, needs_value)
    ("direct_subclasses", "Direct subclasses of a class", (1, 0, 0)),
    ("all_subclasses", "All subclasses (descendants) of a class", (1, 0, 0)),
    ("direct_superclasses", "Direct superclasses of a class", (1, 0, 0)),
    ("all_superclasses", "All superclasses (ancestors) of a class", (1, 0, 0)),
    ("siblings", "Sibling classes of a class", (1, 0, 0)),
    ("instances", "Instances (individuals) of a class", (1, 0, 0)),
    ("restriction_value",
     "Classes with restriction:  property [= value]", (0, 1, 1)),
    ("uses_property",
     "Classes that use a property in a restriction", (0, 1, 0)),
    ("roots", "Root classes (no superclass)", (0, 0, 0)),
    ("leaves", "Leaf classes (no subclass)", (0, 0, 0)),
    ("defined", "Defined classes (have an Equivalent To)", (0, 0, 0)),
    ("no_label", "Classes missing an rdfs:label", (0, 0, 0)),
    ("no_comment", "Classes missing an rdfs:comment", (0, 0, 0)),
]
QUERY_LABELS = {key: label for key, label, _ in QUERY_TYPES}


def _has_comment(ont, cls):
    return any(p in ("rdfs:comment", "comment")
               for p, _t in ont.get_annotations(cls))


def run_query(ont, key, cls=None, prop=None, value=None):
    """Run a competency-question query. Returns a list of (text, qname)."""
    def rows(names):
        return [(ofn.local_name(n), n)
                for n in sorted(names, key=lambda x: x.lower())]

    c = None
    if key in ("direct_subclasses", "all_subclasses", "direct_superclasses",
               "all_superclasses", "siblings", "instances"):
        if not cls:
            return []
        c = ont._qname(cls)

    if key == "direct_subclasses":
        return rows(ont.children.get(c, []))
    if key == "all_subclasses":
        return rows(ont.descendants(c))
    if key == "direct_superclasses":
        return rows(ont.parents.get(c, []))
    if key == "all_superclasses":
        return rows(ont.ancestors(c))
    if key == "siblings":
        sibs = set()
        for p in ont.parents.get(c, []):
            sibs |= set(ont.children.get(p, []))
        sibs.discard(c)
        return rows(sibs)
    if key == "instances":
        found = set()
        for ax in ont.refs.get(c, []):
            nd = ax.node
            if (nd.functor == "ClassAssertion" and len(nd.args) == 2
                    and isinstance(nd.args[0], Iri) and nd.args[0].value == c
                    and isinstance(nd.args[1], Iri)):
                found.add(nd.args[1].value)
        return rows(found)
    if key == "roots":
        return rows(ont.roots())
    if key == "leaves":
        return rows(c for c in ont.entities["Class"]
                    if not ont.children.get(c))
    if key == "defined":
        defined = set()
        for ax in ont.doc.axioms():
            if ax.node.functor == "EquivalentClasses":
                for a in ax.node.args:
                    if isinstance(a, Iri):
                        defined.add(a.value)
                        break
        return rows(defined)
    if key == "no_label":
        return rows(c for c in ont.entities["Class"] if not ont.get_label(c))
    if key == "no_comment":
        return rows(c for c in ont.entities["Class"]
                    if not _has_comment(ont, c))
    if key == "uses_property":
        if not prop:
            return []
        p = ont._qname(prop)
        return rows(c for c in ont.entities["Class"]
                    if any(rp == p for rp, _rf in ont.restriction_targets(c)))
    if key == "restriction_value":
        if not prop:
            return []
        p = ont._qname(prop)
        v = ont._qname(value) if value else None
        out = set()
        for c in ont.entities["Class"]:
            for rp, rf in ont.restriction_targets(c):
                if rp == p and (v is None or rf == v):
                    out.add(c)
        return rows(out)
    return []


# ---------------------------------------------------------------------------
# Pitfall scan
# ---------------------------------------------------------------------------

def pitfall_scan(ont):
    """OOPS!-style quality scan. Returns a list of issue dicts."""
    issues = []

    def add(category, severity, entity, message):
        issues.append({"category": category, "severity": severity,
                       "entity": entity, "message": message})

    for v in ont.validate():
        msg = v["message"]
        if "Cycle" in msg:
            cat = "Hierarchy cycle"
        elif "never declared" in msg:
            cat = "Undeclared entity"
        elif "declared" in msg and "times" in msg:
            cat = "Duplicate declaration"
        else:
            cat = "Structural"
        add(cat, v["severity"], v["entity"], msg)

    for c in sorted(ont.entities["Class"]):
        if not ont.get_label(c):
            add("Missing rdfs:label", "warning", c,
                "Class '%s' has no rdfs:label." % ofn.local_name(c))
    for c in sorted(ont.entities["Class"]):
        if not _has_comment(ont, c):
            add("Missing rdfs:comment", "info", c,
                "Class '%s' has no rdfs:comment." % ofn.local_name(c))

    has_domain, has_range = set(), set()
    for ax in ont.doc.axioms():
        f, A = ax.node.functor, ax.node.args
        if f in ("ObjectPropertyDomain", "DataPropertyDomain") \
                and A and isinstance(A[0], Iri):
            has_domain.add(A[0].value)
        elif f in ("ObjectPropertyRange", "DataPropertyRange") \
                and A and isinstance(A[0], Iri):
            has_range.add(A[0].value)
    for p in sorted(ont.entities["ObjectProperty"]
                    | ont.entities["DataProperty"]):
        if p not in has_domain:
            add("Property without domain", "warning", p,
                "Property '%s' has no declared domain." % ofn.local_name(p))
        if p not in has_range:
            add("Property without range", "warning", p,
                "Property '%s' has no declared range." % ofn.local_name(p))

    by_label = defaultdict(list)
    for c in ont.entities["Class"]:
        lbl = ont.get_label(c)
        if lbl:
            by_label[lbl.strip().lower()].append(c)
    for lbl, members in sorted(by_label.items()):
        if len(members) > 1:
            add("Duplicate label", "warning", "",
                "%d classes share the label '%s': %s"
                % (len(members), lbl,
                   ", ".join(ofn.local_name(m) for m in sorted(members))))

    disjoint_sets = []
    for ax in ont.doc.axioms():
        if ax.node.functor == "DisjointClasses":
            members = frozenset(a.value for a in ax.node.args
                                if isinstance(a, Iri))
            if members:
                disjoint_sets.append(members)
    for parent in sorted(ont.entities["Class"]):
        kids = set(ont.children.get(parent, []))
        if len(kids) >= 2 and not any(kids <= ds for ds in disjoint_sets):
            add("Siblings not disjoint", "info", parent,
                "The %d subclasses of '%s' are not declared pairwise "
                "disjoint." % (len(kids), ofn.local_name(parent)))

    return issues


# ---------------------------------------------------------------------------
# Metrics, outline, glossary, diff
# ---------------------------------------------------------------------------

def metrics(ont):
    """A detailed metrics report as a list of (label, value) pairs."""
    s = ont.statistics()
    axioms = ont.doc.axioms()
    fc = Counter(a.functor for a in axioms)
    classes = ont.entities["Class"]
    nclass = len(classes) or 1

    labelled = sum(1 for c in classes if ont.get_label(c))
    commented = sum(1 for c in classes if _has_comment(ont, c))

    defined = set()
    for a in axioms:
        if a.functor == "EquivalentClasses":
            for x in a.node.args:
                if isinstance(x, Iri):
                    defined.add(x.value)
                    break
    restr = sum(1 for a in axioms
                if a.functor == "SubClassOf" and len(a.node.args) == 2
                and not isinstance(a.node.args[1], Iri))
    parents_with_kids = [p for p in classes if ont.children.get(p)]
    avg_kids = (sum(len(ont.children[p]) for p in parents_with_kids)
                / len(parents_with_kids)) if parents_with_kids else 0.0

    return [
        ("Classes", s["Classes"]),
        ("Object properties", s["Object properties"]),
        ("Data properties", s["Data properties"]),
        ("Named individuals", s["Named individuals"]),
        ("", ""),
        ("Total axioms", s["Total axioms"]),
        ("   SubClassOf axioms", fc.get("SubClassOf", 0)),
        ("   EquivalentClasses axioms", fc.get("EquivalentClasses", 0)),
        ("   DisjointClasses axioms", fc.get("DisjointClasses", 0)),
        ("   Restriction (anonymous superclass) axioms", restr),
        ("   Annotation assertions", fc.get("AnnotationAssertion", 0)),
        ("", ""),
        ("Root classes", s["Root classes"]),
        ("Leaf classes", s["Leaf classes"]),
        ("Max hierarchy depth", s["Max hierarchy depth"]),
        ("Average subclasses per parent class", round(avg_kids, 2)),
        ("Defined classes (with Equivalent To)", len(defined)),
        ("Primitive classes (no Equivalent To)", s["Classes"] - len(defined)),
        ("", ""),
        ("Classes with rdfs:label",
         "%d  (%d%%)" % (labelled, round(100 * labelled / nclass))),
        ("Classes with rdfs:comment",
         "%d  (%d%%)" % (commented, round(100 * commented / nclass))),
    ]


def hierarchy_outline(ont, root=None, with_labels=True):
    """An indented text outline of the class hierarchy."""
    lines = []

    def walk(cls, depth, path):
        label = ont.get_label(cls) if with_labels else ""
        suffix = "  -  " + label if label else ""
        cycle = "   (cycle - stopped)" if cls in path else ""
        lines.append("    " * depth + ofn.local_name(cls) + suffix + cycle)
        if cls in path:
            return
        for child in sorted(ont.children.get(cls, [])):
            walk(child, depth + 1, path | {cls})

    roots = [ont._qname(root)] if root else ont.roots()
    for r in roots:
        walk(r, 0, frozenset())
    return "\n".join(lines)


def glossary(ont, fmt="text"):
    """A class glossary / data dictionary as 'text', 'markdown' or 'csv'."""
    rows = []
    for c in sorted(ont.entities["Class"],
                    key=lambda x: ofn.local_name(x).lower()):
        comment = ""
        for p, t in ont.get_annotations(c):
            if p in ("rdfs:comment", "comment"):
                comment = t
                break
        rows.append((ofn.local_name(c), ont.get_label(c), comment))

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Class", "Label", "Comment"])
        writer.writerows(rows)
        return buf.getvalue()

    if fmt == "markdown":
        out = ["| Class | Label | Comment |", "|---|---|---|"]
        for name, label, comment in rows:
            out.append("| %s | %s | %s |"
                       % (name,
                          label.replace("|", "\\|"),
                          comment.replace("|", "\\|").replace("\n", " ")))
        return "\n".join(out)

    out = []
    for name, label, comment in rows:
        out.append(name + ("  -  " + label if label else ""))
        if comment:
            out.append("    " + comment.replace("\n", "\n    "))
        out.append("")
    return "\n".join(out)


def diff_files(ont, other_path):
    """Axiom-level diff of the current ontology against another file."""
    other = ofn.load(other_path)

    def axioms(doc):
        return Counter(a.node.render() for a in doc.axioms())

    def declarations(doc):
        found = set()
        for a in doc.axioms():
            if a.node.functor == "Declaration" and a.node.args:
                inner = a.node.args[0]
                if isinstance(inner, Node) and inner.args \
                        and isinstance(inner.args[0], Iri):
                    found.add((inner.functor, inner.args[0].value))
        return found

    cur_ax, oth_ax = axioms(ont.doc), axioms(other)
    cur_d, oth_d = declarations(ont.doc), declarations(other)
    return {
        "other": other_path,
        "added_entities": sorted(cur_d - oth_d),
        "removed_entities": sorted(oth_d - cur_d),
        "added_axioms": sorted((cur_ax - oth_ax).elements()),
        "removed_axioms": sorted((oth_ax - cur_ax).elements()),
    }
