"""
webapp/server.py - FastAPI server for the Prestige — OWL ontology editor.

A thin REST + static-file layer over the headless `core` package. One
Ontology instance lives in the server process; the browser is the view.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import Ontology, ModelError
from core import ofn, examtools, graphview, reasoner

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
PROJECT = HERE.parent


# ---------------------------------------------------------------------------
# Lifespan: load the ontology once, hold it on app.state
# ---------------------------------------------------------------------------

class _State:
    ont: Optional[Ontology] = None


state = _State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No auto-load - the user uploads their ontology from the web UI.
    state.ont = None
    yield


app = FastAPI(title="Prestige — OWL ontology editor", lifespan=lifespan)


@app.exception_handler(ModelError)
def _model_error(_req, exc: ModelError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

class _NoCacheStaticFiles(StaticFiles):
    """Same as StaticFiles but tells the browser never to cache - so edits
    to styles.css / app.js show up on every reload during development."""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


app.mount("/static", _NoCacheStaticFiles(directory=str(STATIC)), name="static")


_INDEX_CACHE = {"html": None, "v": None}

@app.get("/")
def index():
    """Serve index.html with a fresh cache-busting version on every load."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    v = str(int(time.time() * 1000))
    html = re.sub(r"\?v=[^\"']+", "?v=" + v, html)
    return Response(content=html,
                    media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ont() -> Ontology:
    if state.ont is None:
        raise HTTPException(
            status_code=409,
            detail="No ontology loaded - upload one via POST /api/load first.")
    return state.ont


def _ok(message: str = "OK", **extra):
    return {"ok": True, "message": message, "state": _state_dict(), **extra}


def _state_dict():
    o = state.ont
    if o is None:
        return {"loaded": False, "path": None, "filename": None,
                "dirty": False, "can_undo": False, "can_redo": False,
                "counts": {}, "prefixes": {}, "ontology_iri": None,
                "version_iri": None}
    return {
        "loaded": True,
        "path": o.path,
        "filename": os.path.basename(o.path) if o.path else None,
        "dirty": o.dirty,
        "can_undo": o.can_undo(),
        "can_redo": o.can_redo(),
        "counts": o.statistics(),
        "prefixes": o.doc.prefixes,
        "ontology_iri": o.doc.ontology_iri,
        "version_iri": o.doc.version_iri,
    }


def _tree_node(o: Ontology, qname: str, path: frozenset, counter: list):
    counter[0] += 1
    cycle = qname in path
    children = [] if cycle else [
        _tree_node(o, c, path | {qname}, counter)
        for c in sorted(o.children.get(qname, []), key=str.lower)
    ]
    return {
        "id": "n%d" % counter[0],
        "qname": qname,
        "name": ofn.local_name(qname),
        "label": o.get_label(qname),
        "cycle": cycle,
        "children": children,
    }


def _entity_list(o: Ontology, kind: str):
    if kind == "Class":
        names = o.classes()
    elif kind == "ObjectProperty":
        names = o.object_properties()
    elif kind == "DataProperty":
        names = o.data_properties()
    elif kind == "NamedIndividual":
        names = o.individuals()
    else:
        raise HTTPException(400, "unknown kind %r" % kind)
    return [{"qname": q, "name": ofn.local_name(q), "label": o.get_label(q)}
            for q in names]


def _describe(o: Ontology, qname: str):
    qname = o._qname(qname)
    # Allow declared entities AND referenced annotation props / datatypes
    # (e.g. rdfs:label, xsd:integer) which don't carry an explicit Declaration.
    if o.kind_of(qname) is None:
        raise HTTPException(404, "entity not found: %s" % qname)
    kind, sections = o.describe(qname)
    return {
        "qname": qname,
        "name": ofn.local_name(qname),
        "kind": kind,
        "label": o.get_label(qname),
        "annotations": [{"prop": p, "text": t} for p, t in o.get_annotations(qname)],
        "sections": [
            {"title": title, "rows": rows}
            for title, rows in sections
        ],
        "appears_in": [" ".join(line.split())
                       for line in o.referencing_axioms(qname)],
        "parents": sorted(o.parents.get(qname, [])),
        "children": sorted(o.children.get(qname, [])),
    }


# ---------------------------------------------------------------------------
# Load / upload
# ---------------------------------------------------------------------------

@app.post("/api/load")
async def api_load(file: UploadFile = File(...)):
    """Upload an .owl file - it becomes the working ontology."""
    raw_name = (file.filename or "uploaded.owl").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if not raw_name:
        raise HTTPException(400, "Empty filename.")
    target = PROJECT / raw_name
    # Don't silently overwrite an existing file - keep a timestamped copy.
    if target.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(target, target.with_suffix(target.suffix + ".%s.uploaded.bak" % stamp))
    content = await file.read()
    target.write_bytes(content)
    try:
        state.ont = Ontology.load(str(target))
    except Exception as exc:
        raise HTTPException(400, "Could not parse %s: %s" % (raw_name, exc))
    return _ok("Loaded %s" % raw_name)


# ---------------------------------------------------------------------------
# State, tree, entities, entity detail
# ---------------------------------------------------------------------------

@app.get("/api/state")
def api_state():
    return _state_dict()


@app.get("/api/tree")
def api_tree():
    o = _ont()
    counter = [0]
    return {"roots": [_tree_node(o, r, frozenset(), counter)
                       for r in o.roots()]}


@app.get("/api/entities")
def api_entities():
    o = _ont()
    def row(q): return {"qname": q, "name": ofn.local_name(q),
                         "label": o.get_label(q)}
    return {
        "classes": _entity_list(o, "Class"),
        "object_properties": _entity_list(o, "ObjectProperty"),
        "data_properties": _entity_list(o, "DataProperty"),
        "individuals": _entity_list(o, "NamedIndividual"),
        "annotation_properties": [row(q) for q in o.annotation_properties()],
        "datatypes": [row(q) for q in o.datatypes()],
    }


@app.get("/api/entity")
def api_entity(qname: str):
    return _describe(_ont(), qname)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

class CreateClassIn(BaseModel):
    name: str
    parents: List[str] = []
    label: Optional[str] = ""
    comment: Optional[str] = ""


@app.post("/api/class")
def api_create_class(body: CreateClassIn):
    o = _ont()
    qname = o.create_class(body.name, parents=body.parents,
                           label=body.label, comment=body.comment)
    return _ok("Class created", selected=qname)


class MoveIn(BaseModel):
    name: str
    parents: List[str]


@app.post("/api/move")
def api_move(body: MoveIn):
    o = _ont()
    o.move_class(body.name, body.parents)
    return _ok("Class moved", selected=o._qname(body.name))


class RenameIn(BaseModel):
    old: str
    new: str


@app.post("/api/rename")
def api_rename(body: RenameIn):
    o = _ont()
    o.rename_entity(body.old, body.new)
    return _ok("Entity renamed", selected=o._qname(body.new))


class DeleteIn(BaseModel):
    name: str
    mode: str = "reparent"


@app.post("/api/delete")
def api_delete(body: DeleteIn):
    o = _ont()
    removed = o.delete_class(body.name, mode=body.mode)
    return _ok("Class deleted (%d axioms removed)" % removed)


class ParentIn(BaseModel):
    name: str
    parent: str


@app.post("/api/parent")
def api_parent(body: ParentIn):
    o = _ont()
    o.add_parent(body.name, body.parent)
    return _ok("Parent added", selected=o._qname(body.name))


@app.post("/api/unparent")
def api_unparent(body: ParentIn):
    o = _ont()
    o.remove_parent(body.name, body.parent)
    return _ok("Parent removed", selected=o._qname(body.name))


class AnnotationIn(BaseModel):
    entity: str
    prop: str
    text: str
    lang: Optional[str] = "en"


@app.post("/api/annotation")
def api_annotation(body: AnnotationIn):
    o = _ont()
    o.set_annotation(body.entity, body.prop, body.text, lang=body.lang or "en")
    return _ok("Annotation updated", selected=o._qname(body.entity))


class RestrictionIn(BaseModel):
    cls: str
    prop: str
    filler: str
    kind: str   # 'some' | 'only' | 'value' | 'data'


@app.post("/api/restriction")
def api_restriction(body: RestrictionIn):
    o = _ont()
    o.add_restriction(body.cls, body.prop, body.filler, kind=body.kind)
    return _ok("Restriction added", selected=o._qname(body.cls))


class DisjointIn(BaseModel):
    classes: List[str]


@app.post("/api/disjoint")
def api_disjoint(body: DisjointIn):
    o = _ont()
    o.add_disjoint_classes(body.classes)
    return _ok("DisjointClasses axiom added")


class EquivalentIn(BaseModel):
    a: str
    b: str


@app.post("/api/equivalent")
def api_equivalent(body: EquivalentIn):
    o = _ont()
    o.add_equivalent_classes(body.a, body.b)
    return _ok("EquivalentClasses axiom added")


class ObjectPropIn(BaseModel):
    name: str
    domain: Optional[str] = ""
    range: Optional[str] = ""
    characteristics: List[str] = []
    label: Optional[str] = ""
    comment: Optional[str] = ""


@app.post("/api/object-property")
def api_obj_prop(body: ObjectPropIn):
    o = _ont()
    q = o.create_object_property(body.name, domain=body.domain,
                                 range_=body.range,
                                 characteristics=body.characteristics,
                                 label=body.label, comment=body.comment)
    return _ok("Object property created", selected=q)


class DataPropIn(BaseModel):
    name: str
    domain: Optional[str] = ""
    range: Optional[str] = "xsd:string"
    functional: bool = False
    label: Optional[str] = ""
    comment: Optional[str] = ""


@app.post("/api/data-property")
def api_data_prop(body: DataPropIn):
    o = _ont()
    q = o.create_data_property(body.name, domain=body.domain,
                               range_=body.range,
                               functional=body.functional,
                               label=body.label, comment=body.comment)
    return _ok("Data property created", selected=q)


class IndividualIn(BaseModel):
    name: str
    types: List[str] = []


@app.post("/api/individual")
def api_individual(body: IndividualIn):
    o = _ont()
    q = o.create_individual(body.name, types=body.types)
    return _ok("Individual created", selected=q)


class ObjAssertIn(BaseModel):
    individual: str
    prop: str
    target: str


@app.post("/api/object-property-assertion")
def api_obj_assert(body: ObjAssertIn):
    o = _ont()
    o.add_object_property_assertion(body.individual, body.prop, body.target)
    return _ok("Object property assertion added",
               selected=o._qname(body.individual))


class DataAssertIn(BaseModel):
    individual: str
    prop: str
    value: str
    datatype: Optional[str] = None


@app.post("/api/data-property-assertion")
def api_data_assert(body: DataAssertIn):
    o = _ont()
    o.add_data_property_assertion(body.individual, body.prop, body.value,
                                  datatype=body.datatype)
    return _ok("Data property assertion added",
               selected=o._qname(body.individual))


class SameIn(BaseModel):
    individuals: List[str]


@app.post("/api/same-individuals")
def api_same(body: SameIn):
    o = _ont()
    o.add_same_individuals(body.individuals)
    return _ok("SameIndividual axiom added")


@app.post("/api/different-individuals")
def api_different(body: SameIn):
    o = _ont()
    o.add_different_individuals(body.individuals)
    return _ok("DifferentIndividuals axiom added")


class ClassAssertIn(BaseModel):
    individual: str
    type: str


@app.post("/api/class-assertion")
def api_class_assert(body: ClassAssertIn):
    o = _ont()
    o.add_class_assertion(body.individual, body.type)
    return _ok("Class assertion added", selected=o._qname(body.individual))


@app.get("/api/ontology-metadata")
def api_ontology_metadata():
    """Ontology IRI, version IRI, prefixes and any Annotation(...) declared
    at the ontology level (Protege's 'Active ontology' header)."""
    o = _ont()
    anns = []
    for nd in getattr(o.doc, "ontology_annotations", []) or []:
        if not nd.args:
            continue
        # Annotation(prop value) OR Annotation(annotations* prop value)
        a = nd.args
        prop = next((x for x in a if isinstance(x, ofn.Iri)), None)
        val = a[-1] if a else None
        anns.append({
            "prop": prop.value if isinstance(prop, ofn.Iri) else "?",
            "value": (val.lexical if isinstance(val, ofn.Literal)
                      else (val.value if isinstance(val, ofn.Iri) else
                            (val.render() if hasattr(val, "render") else str(val)))),
            "language": (val.lang if isinstance(val, ofn.Literal) else None),
        })
    return {
        "ontology_iri": o.doc.ontology_iri,
        "version_iri": o.doc.version_iri,
        "prefixes": o.doc.prefixes,
        "annotations": anns,
    }


class RawAxiomIn(BaseModel):
    text: str


@app.post("/api/raw-axiom")
def api_raw(body: RawAxiomIn):
    o = _ont()
    functor = o.add_raw_axiom(body.text)
    return _ok("Added %s axiom" % functor)


# ---------------------------------------------------------------------------
# Detail-panel row operations (+ Add / Remove buttons)
# ---------------------------------------------------------------------------

class RemoveRelationIn(BaseModel):
    entity: str
    spec: dict


@app.post("/api/remove-relation")
def api_remove_relation(body: RemoveRelationIn):
    o = _ont()
    o.remove_relation(body.entity, body.spec)
    return _ok("Removed", selected=o._qname(body.entity))


class SubPropIn(BaseModel):
    kind: str        # 'object' | 'data' | 'annotation'
    child: str
    parent: str


@app.post("/api/sub-property")
def api_sub_property(body: SubPropIn):
    o = _ont()
    o.add_sub_property(body.kind, body.child, body.parent)
    return _ok("Sub-property added", selected=o._qname(body.child))


class PropDomainRangeIn(BaseModel):
    kind: str        # 'object' | 'data' | 'annotation'
    prop: str
    target: str      # class qname for domain (any kind); class for object
                     # range, datatype for data range, IRI for annotation range


@app.post("/api/property-domain")
def api_property_domain(body: PropDomainRangeIn):
    o = _ont()
    o.add_property_domain(body.kind, body.prop, body.target)
    return _ok("Domain added", selected=o._qname(body.prop))


@app.post("/api/property-range")
def api_property_range(body: PropDomainRangeIn):
    o = _ont()
    o.add_property_range(body.kind, body.prop, body.target)
    return _ok("Range added", selected=o._qname(body.prop))


class CharacteristicIn(BaseModel):
    prop: str
    functor: str    # e.g. "FunctionalObjectProperty"


@app.post("/api/characteristic")
def api_characteristic(body: CharacteristicIn):
    o = _ont()
    o.add_characteristic(body.prop, body.functor)
    return _ok("Characteristic added", selected=o._qname(body.prop))


class InverseIn(BaseModel):
    a: str
    b: str


@app.post("/api/inverse-properties")
def api_inverse(body: InverseIn):
    o = _ont()
    o.add_inverse_properties(body.a, body.b)
    return _ok("Inverse axiom added", selected=o._qname(body.a))


class EquivPropIn(BaseModel):
    kind: str    # 'object' | 'data'
    a: str
    b: str


@app.post("/api/equivalent-properties")
def api_equiv_props(body: EquivPropIn):
    o = _ont()
    o.add_equivalent_properties(body.kind, body.a, body.b)
    return _ok("Equivalent properties added", selected=o._qname(body.a))


# ---------------------------------------------------------------------------
# Undo / redo / save
# ---------------------------------------------------------------------------

@app.post("/api/undo")
def api_undo():
    ok = _ont().undo()
    return _ok("Undone" if ok else "Nothing to undo")


@app.post("/api/redo")
def api_redo():
    ok = _ont().redo()
    return _ok("Redone" if ok else "Nothing to redo")


@app.post("/api/save")
def api_save():
    o = _ont()
    backup = o.save()
    return _ok("Saved",
               backup=os.path.basename(backup) if backup else None)


@app.get("/api/ontology-text")
def api_ontology_text():
    """Current in-memory serialised ontology as plain text. Used by the
    front-end Save-As flow: the browser fetches this and writes it to a
    user-chosen location via the File System Access API."""
    o = _ont()
    return Response(content=o.serialize(),
                    media_type="text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# Validation, statistics, metrics, queries
# ---------------------------------------------------------------------------

@app.get("/api/validate")
def api_validate():
    return {"issues": _ont().validate()}


@app.get("/api/pitfalls")
def api_pitfalls():
    return {"issues": examtools.pitfall_scan(_ont())}


@app.get("/api/statistics")
def api_statistics():
    return _ont().statistics()


@app.get("/api/metrics")
def api_metrics():
    return [{"label": l, "value": v} for l, v in examtools.metrics(_ont())]


@app.get("/api/query-types")
def api_query_types():
    return [{"key": k, "label": l, "needs": list(n)}
            for k, l, n in examtools.QUERY_TYPES]


class QueryIn(BaseModel):
    key: str
    cls: Optional[str] = None
    prop: Optional[str] = None
    value: Optional[str] = None


@app.post("/api/query")
def api_query(body: QueryIn):
    rows = examtools.run_query(_ont(), body.key, cls=body.cls,
                                prop=body.prop, value=body.value)
    return {"rows": [{"text": text, "qname": qname} for text, qname in rows]}


@app.get("/api/outline")
def api_outline(root: Optional[str] = None, labels: bool = True):
    text = examtools.hierarchy_outline(_ont(), root=root, with_labels=labels)
    return Response(content=text, media_type="text/plain")


@app.get("/api/glossary")
def api_glossary(fmt: str = "text"):
    text = examtools.glossary(_ont(), fmt=fmt)
    media = {"csv": "text/csv", "markdown": "text/markdown"}.get(
        fmt, "text/plain")
    return Response(content=text, media_type=media)


@app.post("/api/diff")
async def api_diff(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename or "other.owl")[1] or ".owl"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(await file.read())
        tmp = fh.name
    try:
        d = examtools.diff_files(_ont(), tmp)
    except Exception as exc:
        os.unlink(tmp)
        raise HTTPException(400, "Could not read that file: %s" % exc)
    os.unlink(tmp)
    return {
        "filename": file.filename,
        "added_entities":  [[k, ofn.local_name(n)] for k, n in d["added_entities"]],
        "removed_entities":[[k, ofn.local_name(n)] for k, n in d["removed_entities"]],
        "added_axioms":   d["added_axioms"],
        "removed_axioms": d["removed_axioms"],
    }


# ---------------------------------------------------------------------------
# Reasoner + SPARQL (need owlready2, sometimes Java)
# ---------------------------------------------------------------------------

@app.get("/api/reasoners")
def api_reasoners():
    """Availability and requirements for HermiT / Pellet / BORN."""
    return reasoner.reasoners_available()


class ReasonIn(BaseModel):
    name: str = "hermit"           # 'hermit' | 'pellet' | 'born'
    born_file: Optional[str] = None  # path to a BORN .owl (BORN only)


@app.post("/api/reason")
def api_reason(body: ReasonIn | None = None):
    name = (body.name if body else "hermit").lower()
    try:
        if name == "hermit":
            return reasoner.run_reasoner_hermit(_ont())
        if name == "pellet":
            return reasoner.run_reasoner_pellet(_ont())
        if name == "born":
            path = (body.born_file if body and body.born_file
                    else _born_path_for(_ont().path))
            if not path or not os.path.isfile(path):
                raise HTTPException(
                    400,
                    "No BORN ontology found at %r. Click 'Create BORN file' "
                    "first (it makes one next to the loaded ontology)." % path)
            return reasoner.run_reasoner_born(path)
        raise HTTPException(400, "Unknown reasoner '%s'." % name)
    except reasoner.ReasonerUnavailable as exc:
        raise HTTPException(400, str(exc))


def _born_path_for(owl_path: Optional[str]) -> Optional[str]:
    """Default _BORN.owl location next to the given ontology path."""
    if not owl_path:
        return None
    base, ext = os.path.splitext(owl_path)
    return base + "_BORN" + (ext or ".owl")


class CreateBornIn(BaseModel):
    probability: str = "1.0"
    output_path: Optional[str] = None   # defaults to <ontology>_BORN.owl


@app.post("/api/create-born")
def api_create_born(body: CreateBornIn | None = None):
    """Create a BORN-style copy of the current ontology on disk.

    Each SubClassOf axiom gets a probability annotation. The new file is
    written next to the loaded ontology as `<name>_BORN.owl` by default."""
    o = _ont()
    if not o.path:
        raise HTTPException(400, "Save the ontology first so it has a path.")
    prob = (body.probability if body else "1.0")
    out = (body.output_path if body and body.output_path
           else _born_path_for(o.path))
    try:
        path, n = reasoner.create_born_file(o, out, default_probability=prob)
    except Exception as exc:
        raise HTTPException(400, "Could not create BORN file: %s" % exc)
    return {
        "ok": True,
        "path": path,
        "filename": os.path.basename(path),
        "annotated_axioms": n,
        "probability": prob,
        "message": "Created %s  (%d SubClassOf axioms annotated)"
                   % (os.path.basename(path), n),
    }


@app.get("/api/raw-owl")
def api_raw_owl():
    """Full contents of the currently-loaded ontology file (text).
    Used by the AI assistant to ground its answers."""
    o = _ont()
    if not o.path or not os.path.isfile(o.path):
        raise HTTPException(404, "No ontology file on disk.")
    with open(o.path, "r", encoding="utf-8") as fh:
        return Response(content=fh.read(),
                        media_type="text/plain; charset=utf-8")


@app.get("/api/raw-born")
def api_raw_born():
    """Contents of the BORN-format file (if one has been created)."""
    o = _ont()
    if not o.path:
        raise HTTPException(404, "No ontology loaded.")
    born = _born_path_for(o.path)
    if not born or not os.path.isfile(born):
        raise HTTPException(404, "BORN file not created yet.")
    with open(born, "r", encoding="utf-8") as fh:
        return Response(content=fh.read(),
                        media_type="text/plain; charset=utf-8")


@app.get("/api/has-born")
def api_has_born():
    """Quick check used by the AI panel to know if BORN context exists."""
    o = state.ont
    if o is None or not o.path:
        return {"exists": False, "path": None}
    born = _born_path_for(o.path)
    return {"exists": bool(born and os.path.isfile(born)), "path": born}


@app.get("/api/download")
def api_download(path: str):
    """Serve a project-local file for download (used for the BORN file)."""
    p = Path(path).resolve()
    project_root = PROJECT.resolve()
    if project_root not in p.parents and p != project_root:
        raise HTTPException(403, "Forbidden path.")
    if not p.is_file():
        raise HTTPException(404, "File not found.")
    return FileResponse(str(p), filename=p.name,
                        media_type="application/octet-stream")


class SparqlIn(BaseModel):
    query: str


@app.post("/api/sparql")
def api_sparql(body: SparqlIn):
    try:
        world, _onto, skipped = reasoner.build_world(_ont())
        rows = list(world.sparql(body.query))
    except reasoner.ReasonerUnavailable as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, str(exc))

    def cell(v):
        return getattr(v, "name", None) or str(v)

    serialised = [
        [cell(c) for c in (row if isinstance(row, (list, tuple)) else [row])]
        for row in rows
    ]
    return {"rows": serialised, "skipped": skipped}


# ---------------------------------------------------------------------------
# Graph (Graphviz SVG)
# ---------------------------------------------------------------------------

@app.get("/api/graph-engines")
def api_graph_engines():
    return [{"name": n, "desc": d} for n, d in graphview.ENGINES]


@app.get("/api/graph")
def api_graph(focus: Optional[str] = None,
              up: int = 2, down: int = 2,
              whole: bool = False, restrictions: bool = False,
              engine: str = "dot",
              format: str = "svg",
              bg: str = "dark"):
    """Render the graph. `format` = svg|png|pdf, `bg` = dark|light."""
    o = _ont()
    qfocus = o._qname(focus) if focus else None
    if qfocus and qfocus not in o.declared:
        qfocus = None
    fmt = (format or "svg").lower()
    if fmt not in ("svg", "png", "pdf"):
        raise HTTPException(400, "format must be svg, png or pdf")
    if bg not in ("dark", "light"):
        bg = "dark"
    try:
        data = graphview.render_graph(
            o, focus=qfocus, up=up, down=down, whole=whole,
            show_restrictions=restrictions, engine=engine,
            fmt=fmt, bg=bg, dpi=(144 if fmt == "png" else 96))
    except Exception as exc:
        raise HTTPException(400, str(exc))
    media = {"svg": "image/svg+xml", "png": "image/png",
             "pdf": "application/pdf"}[fmt]
    return Response(content=data, media_type=media)
