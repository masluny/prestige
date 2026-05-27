"""
core/dl_query.py — Manchester-syntax DL queries via owlready2 + a reasoner.

Lets users type expressions like:

    RoadSign and not (hasSymbol some Symbol)
    info_min_speed_40 and reg_max_speed_40

…and ask one of these questions:

  • SUBCLASSES   — which named classes are subsumed by the expression
  • INSTANCES    — which named individuals satisfy the expression
  • EQUIVALENTS  — which named classes are logically equivalent
  • SATISFIABLE  — is the expression satisfiable (i.e. has any instance)
  • DISJOINT     — are TWO expressions disjoint (intersection unsatisfiable)

Implementation:
  1. Translate the current ontology model into an in-memory owlready2
     world via `core.reasoner._Translator` (reused for free).
  2. Parse the Manchester text into an owlready2 class-expression.
  3. Define a fresh temporary class equivalent to that expression.
  4. Run the chosen reasoner (HermiT/Pellet) via owlready2.
  5. Inspect the temp class's descendants / equivalents / instances.

The reasoner integration is shared with `core.reasoner` — both modules
honour the user's Java-path override (Settings → External tools).
"""

from __future__ import annotations

import re
from typing import Optional, Tuple, List, Iterable

from . import ofn, reasoner


# ===========================================================================
# Manchester-ish syntax tokenizer + parser
# ===========================================================================

# Tokens
_TK_NAME, _TK_LPAREN, _TK_RPAREN, _TK_AND, _TK_OR, _TK_NOT = range(6)
_TK_SOME, _TK_ONLY, _TK_VALUE, _TK_DOT = 6, 7, 8, 9
_TK_EOF = 99

_KEYWORDS = {
    "and":  _TK_AND, "AND": _TK_AND, "&": _TK_AND,
    "or":   _TK_OR,  "OR":  _TK_OR,  "|": _TK_OR,
    "not":  _TK_NOT, "NOT": _TK_NOT,
    "some": _TK_SOME, "SOME": _TK_SOME, "∃": _TK_SOME,
    "only": _TK_ONLY, "ONLY": _TK_ONLY, "∀": _TK_ONLY,
    "value": _TK_VALUE, "VALUE": _TK_VALUE,
}


def _tokenize(text: str):
    """Yield (kind, value) tokens. Names match `[A-Za-z_][\\w:.-]*`."""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            yield (_TK_LPAREN, "("); i += 1; continue
        if ch == ")":
            yield (_TK_RPAREN, ")"); i += 1; continue
        if ch == "&":
            yield (_TK_AND, "&"); i += 1; continue
        if ch == "|":
            yield (_TK_OR, "|"); i += 1; continue
        if ch == "!" or ch == "¬":
            yield (_TK_NOT, ch); i += 1; continue
        # multi-char name or keyword
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_:.-"):
                j += 1
            word = text[i:j]
            i = j
            if word in _KEYWORDS:
                yield (_KEYWORDS[word], word)
            else:
                yield (_TK_NAME, word)
            continue
        raise SyntaxError("Unexpected character %r at position %d" % (ch, i))
    yield (_TK_EOF, "")


class _Parser:
    """Tiny recursive-descent parser for Manchester-like class expressions.

    Grammar (precedence: high → low):

        Atom        := NAME | "(" Expr ")"
        Restriction := NAME some|only|value Atom | NAME "." NAME ...     -- simple
        Unary       := "not" Unary | Restriction
        And         := Unary ("and" Unary)*
        Or          := And  ("or"  And)*
        Expr        := Or
    """
    def __init__(self, text, onto, world):
        self.tokens = list(_tokenize(text))
        self.pos = 0
        self.onto = onto
        self.world = world

    # -- token plumbing ------------------------------------------------
    def peek(self):
        return self.tokens[self.pos][0]

    def take(self):
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def expect(self, kind, label):
        if self.peek() != kind:
            tok = self.tokens[self.pos][1]
            raise SyntaxError("expected %s, got %r" % (label, tok))
        return self.take()

    # -- entity lookup ------------------------------------------------
    def _lookup_entity(self, name):
        """Find an owlready2 entity by short name, qname, or full IRI.
        Returns the entity or raises a clear error."""
        # Try direct attribute (short name without colon)
        local = name.split(":")[-1]
        for cand in (name, local):
            e = self.onto.search_one(iri="*#" + cand)
            if e is not None:
                return e
            try:
                e = getattr(self.onto, cand, None)
                if e is not None:
                    return e
            except Exception:
                pass
        # Try world-wide lookup by partial IRI
        try:
            for e in self.world.search(iri="*#" + local):
                return e
        except Exception:
            pass
        raise SyntaxError("Unknown entity: %r" % name)

    # -- grammar rules -------------------------------------------------
    def expr(self):
        return self.or_expr()

    def or_expr(self):
        left = self.and_expr()
        while self.peek() == _TK_OR:
            self.take()
            right = self.and_expr()
            left = left | right
        return left

    def and_expr(self):
        left = self.unary_expr()
        while self.peek() == _TK_AND:
            self.take()
            right = self.unary_expr()
            left = left & right
        return left

    def unary_expr(self):
        if self.peek() == _TK_NOT:
            self.take()
            inner = self.unary_expr()
            import owlready2 as _ow
            return _ow.Not(inner)
        return self.atom_or_restriction()

    def atom_or_restriction(self):
        if self.peek() == _TK_LPAREN:
            self.take()
            e = self.expr()
            self.expect(_TK_RPAREN, ")")
            return e
        if self.peek() == _TK_NAME:
            _, name = self.take()
            ent = self._lookup_entity(name)
            # Restriction: `prop some|only|value <filler>`
            kw = self.peek()
            if kw in (_TK_SOME, _TK_ONLY, _TK_VALUE):
                kind = self.take()[1].lower()
                # filler is an atom, NOT a full or-expression (Manchester precedence)
                filler = self.atom_or_restriction()
                if kind == "some":
                    return ent.some(filler)
                if kind == "only":
                    return ent.only(filler)
                if kind == "value":
                    return ent.value(filler)
            return ent
        tok = self.tokens[self.pos][1]
        raise SyntaxError("unexpected token %r" % tok)


def parse_expression(text, onto, world):
    """Parse Manchester-ish text into an owlready2 expression."""
    p = _Parser(text, onto, world)
    e = p.expr()
    if p.peek() != _TK_EOF:
        rest = " ".join(t[1] for t in p.tokens[p.pos:])
        raise SyntaxError("unexpected trailing text: %r" % rest)
    return e


# ===========================================================================
# Reasoner-backed evaluation
# ===========================================================================

VALID_QUERY_TYPES = ("subclasses", "instances", "equivalents",
                     "satisfiable", "superclasses")


def _build_world(model):
    """Translate our ofn model -> owlready2 world+onto."""
    owl = reasoner._import_owlready()
    world, onto, ents, skipped = reasoner._Translator(model).build()
    return owl, world, onto, ents, skipped


def _run_sync_reasoner(owl, world, onto, reasoner_name="hermit"):
    if not reasoner._java_works():
        raise reasoner.ReasonerUnavailable(
            "No working Java runtime found - DL queries need Java.\n"
            "Install Java 25, e.g.:  brew install --cask temurin@25\n"
            "Older Javas crash on the bundled Jena library used by Pellet "
            "(class file version 69.0 means JVM >= 25 is required).")
    with onto:
        try:
            if reasoner_name == "pellet":
                owl.sync_reasoner_pellet(
                    world, infer_property_values=False,
                    infer_data_property_values=False, debug=0)
            else:
                owl.sync_reasoner_hermit(
                    world, infer_property_values=False, debug=0)
        except TypeError:
            if reasoner_name == "pellet":
                owl.sync_reasoner_pellet([onto])
            else:
                owl.sync_reasoner_hermit([onto])


def _qname_of(entity):
    """roadsigns:Foo style name for a class/individual."""
    iri = getattr(entity, "iri", None) or ""
    if "#" in iri:
        local = iri.rsplit("#", 1)[1]
    elif "/" in iri:
        local = iri.rsplit("/", 1)[1]
    else:
        local = str(entity.name)
    pfx = ofn.prefix_of(getattr(entity, "name", "") or "") or "roadsigns"
    return "%s:%s" % (pfx, local) if pfx else local


def run_query(model, expression, query_type="subclasses",
              reasoner_name="hermit"):
    """Execute a DL query.

    Returns a dict like:
      {"results": [qname, ...], "skipped": N, "reasoner": "HermiT", "type": ...}

    For `satisfiable` the dict instead has a boolean `satisfiable` field.

    Optimisation: if the expression parses to a single named class, we
    skip the temp-class + reasoner detour and read its descendants /
    ancestors / instances directly. That's what Protégé's DL-Query tab
    does too for plain class names.
    """
    if query_type not in VALID_QUERY_TYPES:
        raise ValueError("Unknown DL query type: %r" % query_type)
    owl, world, onto, ents, skipped = _build_world(model)
    expr = parse_expression(expression, onto, world)

    # Fast path: bare named class — no reasoner needed for asserted info.
    is_named = isinstance(expr, owl.ThingClass)

    out = {"reasoner": reasoner_name.capitalize() if not is_named else "(asserted)",
           "skipped": skipped, "type": query_type, "expression": expression}

    if is_named:
        target = expr
    else:
        with onto:
            TempClass = owl.types.new_class(
                "__DLQuery_Temp__", (owl.Thing,))
            TempClass.equivalent_to = [expr]
        _run_sync_reasoner(owl, world, onto, reasoner_name=reasoner_name)
        target = TempClass

    def collect_iris(items):
        out_names = []
        for c in items:
            try:
                if c is owl.Nothing or c is owl.Thing:
                    continue
                # Skip our internal temp helpers
                nm = getattr(c, "name", "") or ""
                if nm.startswith("__DLQuery"):
                    continue
                out_names.append(_qname_of(c))
            except Exception:
                pass
        return sorted(set(out_names))

    if query_type == "satisfiable":
        try:
            unsat = list(world.inconsistent_classes())
        except Exception:
            unsat = []
        out["satisfiable"] = target not in unsat
        return out

    if query_type == "subclasses":
        # `descendants()` reads asserted + inferred (post-sync_reasoner).
        # For a complex expression we ALSO query subclass_of so the
        # reasoner's freshly-inferred edges are picked up.
        items = set(target.descendants(include_self=False))
        try:
            items.update(world.search(subclass_of=target))
        except Exception:
            pass
        out["results"] = collect_iris(items)
        return out

    if query_type == "superclasses":
        items = set(target.ancestors(include_self=False))
        out["results"] = collect_iris(items)
        return out

    if query_type == "equivalents":
        items = []
        try:
            items.extend(target.equivalent_to)
        except Exception:
            pass
        out["results"] = [x for x in collect_iris(items)
                          if x != _qname_of(target)] if is_named else collect_iris(items)
        return out

    if query_type == "instances":
        items = []
        try:
            items.extend(target.instances())
        except Exception:
            pass
        out["results"] = collect_iris(items)
        return out

    out["results"] = []
    return out


def check_disjoint(model, class_a, class_b, reasoner_name="hermit"):
    """Are two named classes disjoint? Returns dict with `disjoint` bool."""
    owl, world, onto, ents, skipped = _build_world(model)
    a_iri = model._qname(class_a)
    b_iri = model._qname(class_b)
    a = onto.search_one(iri=a_iri) or onto.search_one(iri="*#" + ofn.local_name(a_iri))
    b = onto.search_one(iri=b_iri) or onto.search_one(iri="*#" + ofn.local_name(b_iri))
    if a is None or b is None:
        raise SyntaxError("Class not found: %s" % (class_a if a is None else class_b))

    with onto:
        TempClass = owl.types.new_class("__DLQuery_DisjointProbe__",
                                         (owl.Thing,))
        TempClass.equivalent_to = [a & b]

    _run_sync_reasoner(owl, world, onto, reasoner_name=reasoner_name)

    try:
        unsat = list(world.inconsistent_classes())
    except Exception:
        unsat = []

    is_unsat = TempClass in unsat
    return {
        "disjoint": is_unsat,
        "explanation": (
            "Yes — the intersection (%s and %s) is unsatisfiable, so the "
            "two classes share no individuals." % (
                ofn.local_name(a_iri), ofn.local_name(b_iri))
            if is_unsat else
            "No — the reasoner found the intersection (%s and %s) is "
            "satisfiable, so a hypothetical individual could belong to "
            "both classes." % (
                ofn.local_name(a_iri), ofn.local_name(b_iri))
        ),
        "reasoner": reasoner_name.capitalize(),
        "skipped": skipped,
    }
