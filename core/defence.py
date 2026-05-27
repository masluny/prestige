"""
core/defence.py — Project Defence rehearsal helpers.

Encapsulates the four exercises from the Vienna Convention road-signs
project defence rehearsal:

  1) Sign coverage  — does the ontology have a class for each Vienna
     Convention warning sign (P-1 .. P-50)?
  2) Signs without symbols  — DL-style query: warning signs that have
     no `hasSymbol some <Symbol>` axiom (i.e. "blank" pictograms like the
     yield / give-way and no-priority signs).
  3) Import alignment  — merge another `.owl` file (e.g. the MTDS
     alignment ontology) into the current model, axiom by axiom.
  4) Disjointness check  — verify that two classes are disjoint (uses
     the DL-query module + reasoner).

This module is pure logic; the FastAPI server in webapp/server.py wraps
each function as an HTTP endpoint.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import List, Dict, Optional

from . import ofn
from .ofn import Node, Iri
from . import dl_query


# ---------------------------------------------------------------------------
# 1) Sign coverage — Vienna Convention warning signs
# ---------------------------------------------------------------------------

# Canonical sign codes + their Spanish description from the project's
# rehearsal slide. (The English description is informal — Spanish is the
# canonical label of the Vienna Convention's Spanish translation.)
VIENNA_SIGNS = [
    # (code, Spanish description, English keywords for fuzzy match)
    ("P-1",   "INTERSECCIÓN CON PRIORIDAD",                                       ["intersection", "priority", "junction", "crossroads"]),
    ("P-1a",  "INTERSECCIÓN CON PRIORIDAD SOBRE VÍA A LA DERECHA",                ["intersection", "priority", "right"]),
    ("P-1b",  "INTERSECCIÓN CON PRIORIDAD SOBRE VÍA A LA IZQUIERDA",              ["intersection", "priority", "left"]),
    ("P-1c",  "INTERSECCIÓN CON PRIORIDAD SOBRE INCORPORACIÓN POR LA DERECHA",    ["intersection", "merging", "merge", "right"]),
    ("P-1d",  "INTERSECCIÓN CON PRIORIDAD SOBRE INCORPORACIÓN POR LA IZQUIERDA",  ["intersection", "merging", "merge", "left"]),
    ("P-2",   "INTERSECCIÓN CON PRIORIDAD A LA DERECHA",                          ["yield", "give", "way", "priority", "right"]),
    ("P-3",   "SEMÁFOROS",                                                        ["traffic", "signal", "light", "lights"]),
    ("P-4",   "INTERSECCIÓN CON CIRCULACIÓN GIRATORIA",                           ["roundabout", "circle", "rotary"]),
    ("P-5",   "PUENTE MÓVIL",                                                     ["movable", "moveable", "draw", "bridge"]),
    ("P-6",   "CRUCE DE TRANVÍA",                                                 ["tram", "tramway", "crossing", "streetcar"]),
    ("P-7",   "PASO A NIVEL CON BARRERAS",                                        ["level", "crossing", "barrier", "barriers", "railroad", "railway", "rail"]),
    ("P-8",   "PASO A NIVEL SIN BARRERAS",                                        ["level", "crossing", "barriers", "railroad", "railway", "rail"]),
    ("P-9a",  "PROXIMIDAD DE UN PASO A NIVEL (LADO DERECHO)",                     ["proximity", "approach", "level", "crossing", "right"]),
    ("P-9b",  "APROXIMACIÓN DE UN PASO A NIVEL (LADO DERECHO)",                   ["approach", "level", "crossing", "right"]),
    ("P-9c",  "CERCANÍA DE UN PASO A NIVEL (LADO DERECHO)",                       ["near", "level", "crossing", "right"]),
    ("P-10a", "PROXIMIDAD DE UN PASO A NIVEL (LADO IZQUIERDO)",                   ["proximity", "approach", "level", "crossing", "left"]),
    ("P-10b", "APROXIMACIÓN DE UN PASO A NIVEL (LADO IZQUIERDO)",                 ["approach", "level", "crossing", "left"]),
    ("P-10c", "CERCANÍA DE UN PASO A NIVEL (LADO IZQUIERDO)",                     ["near", "level", "crossing", "left"]),
    ("P-11",  "SITUACIÓN DE UN PASO A NIVEL SIN BARRERAS",                        ["level", "crossing", "barrier", "railroad", "railway"]),
    ("P-11a", "PASO A NIVEL DE MÁS DE UNA VÍA FÉRREA",                            ["level", "crossing", "multiple", "tracks", "rail"]),
    ("P-12",  "AEROPUERTO",                                                       ["airport", "airfield", "aerodrome", "aircraft", "plane"]),
    ("P-13a", "CURVA PELIGROSA HACIA LA DERECHA",                                 ["dangerous", "curve", "bend", "right"]),
    ("P-13b", "CURVA PELIGROSA HACIA LA IZQUIERDA",                               ["dangerous", "curve", "bend", "left"]),
    ("P-14a", "CURVAS PELIGROSAS HACIA LA DERECHA",                               ["dangerous", "curves", "bends", "right"]),
    ("P-14b", "CURVAS PELIGROSAS HACIA LA IZQUIERDA",                             ["dangerous", "curves", "bends", "left"]),
    ("P-15",  "PERFIL IRREGULAR",                                                 ["uneven", "irregular", "road", "rough"]),
    ("P-15a", "RESALTO",                                                          ["hump", "bump", "speed", "ridge"]),
    ("P-15b", "BADÉN",                                                            ["dip", "depression", "ford"]),
    ("P-16a", "BAJADA CON FUERTE PENDIENTE",                                      ["steep", "descent", "downhill", "down", "slope"]),
    ("P-16b", "SUBIDA CON FUERTE PENDIENTE",                                      ["steep", "ascent", "uphill", "up", "slope"]),
    ("P-17",  "ESTRECHAMIENTO DE LA CALZADA",                                     ["narrows", "narrowing", "narrow", "road", "carriageway"]),
    ("P-17a", "ESTRECHAMIENTO POR LA DERECHA",                                    ["narrows", "narrowing", "right"]),
    ("P-17b", "ESTRECHAMIENTO POR LA IZQUIERDA",                                  ["narrows", "narrowing", "left"]),
    ("P-18",  "OBRAS",                                                            ["roadworks", "works", "construction", "men", "working"]),
    ("P-19",  "PAVIMENTO DESLIZANTE",                                             ["slippery", "skid", "pavement", "road"]),
    ("P-20",  "PEATONES",                                                         ["pedestrian", "pedestrians", "walking"]),
    ("P-21",  "NIÑOS",                                                            ["children", "school", "kids"]),
    ("P-22",  "CICLISTAS",                                                        ["cyclist", "cyclists", "bicycle", "bike"]),
    ("P-23",  "PASO DE ANIMALES DOMÉSTICOS",                                      ["domestic", "animal", "animals", "cattle", "livestock", "farm"]),
    ("P-24",  "PASO DE ANIMALES EN LIBERTAD",                                     ["wild", "animal", "animals", "deer", "moose"]),
    ("P-25",  "CIRCULACIÓN EN LOS DOS SENTIDOS",                                  ["two", "way", "traffic", "twoway", "bidirectional"]),
    ("P-26",  "DESPRENDIMIENTO",                                                  ["falling", "rocks", "landslide", "stones"]),
    ("P-27",  "MUELLE",                                                           ["quay", "wharf", "dock", "embankment"]),
    ("P-28",  "PROYECCIÓN DE GRAVILLA",                                           ["loose", "gravel", "chippings", "stones"]),
    ("P-29",  "VIENTO TRANSVERSAL",                                               ["side", "wind", "crosswind", "winds"]),
    ("P-30",  "ESCALÓN LATERAL",                                                  ["lateral", "drop", "shoulder", "edge", "off"]),
    ("P-31",  "CONGESTIÓN",                                                       ["congestion", "traffic", "jam", "queue"]),
    ("P-32",  "OBSTRUCCIÓN EN LA CALZADA",                                        ["obstruction", "obstacle", "blockage"]),
    ("P-33",  "VISIBILIDAD REDUCIDA",                                             ["reduced", "visibility", "fog", "low"]),
    ("P-34",  "PAVIMENTO DESLIZANTE POR HIELO O NIEVE",                           ["ice", "snow", "slippery", "winter"]),
    ("P-50",  "OTROS PELIGROS",                                                   ["other", "danger", "hazard", "general", "exclamation"]),
]


def _strip_diacritics(s: str) -> str:
    """Lowercase + strip Spanish accents."""
    import unicodedata
    nfd = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _tokenize(s: str):
    """Split into lowercase, diacritics-free alphanumeric tokens."""
    return [t for t in re.split(r"[^a-z0-9]+", _strip_diacritics(s)) if t]


def _code_regex(code: str) -> re.Pattern:
    """Build a regex that matches the P-code as a distinct token in a
    string of any naming convention (Pascal, snake, kebab, etc.).

    `P-1`   → matches  p-1, p_1, P1, p.1, "P 1", but NOT "loop270".
    `P-9a`  → matches  p-9a, P9a, p_9_a, but NOT p9 alone.
    """
    base, suffix = code.lower(), ""
    m = re.match(r"^(p-?\d+)([a-z]?)$", code.lower())
    if m:
        base, suffix = m.group(1).replace("-", "[-_]?"), m.group(2)
    else:
        base = re.escape(code.lower()).replace(r"\-", "[-_]?")
    # Match the code preceded by non-alphanumeric (or start) and followed
    # by non-alphanumeric (or end). The suffix letter (if any) is optional
    # in the pattern only when there isn't one; if there is one we require it.
    # We also explicitly require a 'p' that isn't part of a longer word.
    if suffix:
        pat = r"(?:^|[^a-z0-9])" + base + suffix + r"(?:$|[^a-z0-9])"
    else:
        pat = r"(?:^|[^a-z0-9])" + base + r"(?![a-z0-9])"
    return re.compile(pat, re.IGNORECASE)


def sign_coverage(model) -> Dict:
    """For each P-code, return whether the ontology has a matching class.

    Matching strategy (ordered, first hit wins):

      1. The class's local name or label contains the P-code as a
         distinct token, considering common separator variants
         (`P-1`, `P_1`, `P1`, `P.1`, …). Surrounded by word boundaries
         so `P-2` doesn't accidentally match `Loop270DegreeSign`.

      2. The Spanish name's significant tokens (≥ 4 letters, accents
         stripped) overlap with the class's normalised tokens by ≥ half.
    """
    # Cache class info — tokens + raw string for code regex
    classes = []
    for qname in model.entities["Class"]:
        local = ofn.local_name(qname)
        label = (model.get_label(qname) or "").strip()
        raw   = local + " " + label
        classes.append({
            "qname":  qname,
            "tokens": set(_tokenize(raw)),
            "raw":    _strip_diacritics(raw),
        })

    results = []
    for entry in VIENNA_SIGNS:
        if len(entry) == 3:
            code, spanish, english_aliases = entry
        else:
            code, spanish = entry[0], entry[1]
            english_aliases = []
        code_re = _code_regex(code)
        spanish_tokens = [t for t in _tokenize(spanish) if len(t) >= 4]
        STOP = {"con", "por", "para", "sobre", "de", "del", "la", "el",
                "en", "los", "las", "una", "uno"}
        spanish_tokens = [t for t in spanish_tokens if t not in STOP]
        eng_tokens = [t.lower() for t in english_aliases]

        code_matches = []
        eng_matches  = []
        spanish_matches = []
        for c in classes:
            # 1) explicit P-code in name/label
            if code_re.search(c["raw"]):
                code_matches.append(c["qname"]); continue
            # 2) English-alias overlap (≥ 2 keyword hits typically reliable)
            if eng_tokens:
                hits = sum(1 for t in eng_tokens if t in c["tokens"])
                if hits >= 2:
                    eng_matches.append((hits, c["qname"]))
                    continue
            # 3) Spanish-token overlap (lower priority — rarely useful for
            # English ontologies, kept for completeness)
            if spanish_tokens:
                hits = sum(1 for t in spanish_tokens if t in c["tokens"])
                if hits >= max(2, (len(spanish_tokens) + 1) // 2):
                    spanish_matches.append(c["qname"])
        # Sort English matches by hit count (descending), keep top 5.
        eng_matches.sort(reverse=True)
        eng_class_names = [q for _, q in eng_matches]

        matches = (code_matches or eng_class_names or spanish_matches)
        source = ("code" if code_matches else
                  "english" if eng_class_names else
                  "spanish" if spanish_matches else None)
        results.append({
            "code": code,
            "spanish": spanish,
            "english_aliases": english_aliases,
            "found": len(matches) > 0,
            "match_source": source,
            "matches": list(dict.fromkeys(matches))[:5],
        })
    found = sum(1 for r in results if r["found"])
    return {"total": len(results), "covered": found,
            "missing": len(results) - found, "signs": results}


# ---------------------------------------------------------------------------
# 2) Signs without symbols — DL helper
# ---------------------------------------------------------------------------

def signs_without_symbols(model,
                          parent_class: str = "RoadSign",
                          symbol_property: str = "hasSymbol",
                          symbol_class: str = "Symbol",
                          reasoner_name: str = "hermit") -> Dict:
    """Run the DL query:  <parent_class> and not (<symbol_property> some <symbol_class>)

    Returns the list of named subclasses (i.e. specific road-sign classes
    whose definition does NOT require any symbol)."""
    expr = "%s and not (%s some %s)" % (parent_class, symbol_property, symbol_class)
    try:
        r = dl_query.run_query(model, expr, query_type="subclasses",
                               reasoner_name=reasoner_name)
        return {"expression": expr, **r}
    except Exception as exc:
        return {"expression": expr, "error": str(exc), "results": []}


# ---------------------------------------------------------------------------
# 3) Import alignment ontology
# ---------------------------------------------------------------------------

def import_alignment(model, raw_text: str) -> Dict:
    """Parse `raw_text` (OWL Functional Syntax) and append its axioms to
    the current model. Declarations of entities already present are
    skipped to avoid duplicates."""
    try:
        doc = ofn.parse(raw_text)
    except Exception as exc:
        return {"ok": False, "error": "Could not parse alignment file: %s" % exc}

    added = 0
    skipped_dupes = 0
    existing_decls = set(model.declared.keys())
    model._begin()
    for ax in doc.axioms():
        f = ax.node.functor
        # Skip duplicate declarations
        if f == "Declaration" and ax.node.args:
            inner = ax.node.args[0]
            if isinstance(inner, Node) and inner.args:
                target = inner.args[0]
                if isinstance(target, Iri) and target.value in existing_decls:
                    skipped_dupes += 1
                    continue
        # Skip prefix declarations / ontology headers
        if f in ("Prefix", "Ontology"):
            continue
        model._insert_axiom(ax.node, {f})
        added += 1
    model._commit()
    return {"ok": True, "added": added,
            "skipped_duplicate_declarations": skipped_dupes,
            "ontology_iri": doc.ontology_iri,
            "version_iri":  doc.version_iri}


# ---------------------------------------------------------------------------
# 4) Disjointness check — DL helper
# ---------------------------------------------------------------------------

def check_disjoint(model, class_a: str, class_b: str,
                   reasoner_name: str = "hermit") -> Dict:
    """Forwards to dl_query.check_disjoint."""
    return dl_query.check_disjoint(model, class_a, class_b,
                                    reasoner_name=reasoner_name)
