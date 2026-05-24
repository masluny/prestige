"""
model.py - the editable ontology model.

Wraps an `ofn.Document` and exposes high-level, undoable editing operations
(create / move / rename / delete classes, manage properties, individuals,
annotations, restrictions, raw axioms), plus structural validation and
statistics.

Every mutating operation:
  * validates its arguments first and raises ModelError on a problem
    (leaving the document untouched);
  * snapshots the document for undo;
  * rebuilds the lookup indexes and sets the `dirty` flag.

The GUI talks only to this module; this module talks to `ofn`.
"""

import copy
import os
import re
import shutil
import time
from collections import defaultdict

from . import ofn
from .ofn import Node, Iri, Literal, Axiom, Trivia

ENTITY_KINDS = ("Class", "ObjectProperty", "DataProperty",
                "NamedIndividual", "AnnotationProperty", "Datatype")

# Object-property characteristic axioms offered by the editor.
OBJECT_CHARACTERISTICS = (
    "FunctionalObjectProperty", "InverseFunctionalObjectProperty",
    "TransitiveObjectProperty", "SymmetricObjectProperty",
    "AsymmetricObjectProperty", "ReflexiveObjectProperty",
    "IrreflexiveObjectProperty",
)

# Property-characteristic axioms mapped to a friendly display name.
CHARACTERISTIC_LABELS = {
    "FunctionalObjectProperty": "Functional",
    "InverseFunctionalObjectProperty": "Inverse functional",
    "TransitiveObjectProperty": "Transitive",
    "SymmetricObjectProperty": "Symmetric",
    "AsymmetricObjectProperty": "Asymmetric",
    "ReflexiveObjectProperty": "Reflexive",
    "IrreflexiveObjectProperty": "Irreflexive",
    "FunctionalDataProperty": "Functional",
}

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


class ModelError(Exception):
    """Raised when an edit is rejected (invalid name, cycle, missing entity)."""


# ---------------------------------------------------------------------------
# Small AST construction helpers
# ---------------------------------------------------------------------------

def _decl(kind, name):
    return Node("Declaration", [Node(kind, [Iri(name)])])


def _subclass(child, parent):
    return Node("SubClassOf", [Iri(child), Iri(parent)])


def _annotation(prop, subject, text, lang="en"):
    return Node("AnnotationAssertion",
                [Iri(prop), Iri(subject), Literal(text, lang or None)])


def iter_nodes(node):
    """Yield every Node (functional sub-expression) inside an AST node."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, Node):
            yield cur
            stack.extend(cur.args)


# ---------------------------------------------------------------------------
# Ontology model
# ---------------------------------------------------------------------------

class Ontology:
    """An editable view over a parsed functional-syntax document."""

    def __init__(self, doc, path=None):
        self.doc = doc
        self.path = path
        self.dirty = False
        self._undo = []
        self._redo = []
        self.reindex()

    # -- loading ------------------------------------------------------------

    @classmethod
    def load(cls, path):
        return cls(ofn.load(path), path)

    # -- indexing -----------------------------------------------------------

    def reindex(self):
        """Rebuild every lookup index from the current document."""
        self.declared = {}                       # name -> kind
        self.entities = {k: set() for k in ENTITY_KINDS}
        self.dup_declared = defaultdict(int)     # name -> declaration count
        self.parents = defaultdict(list)         # class -> [named superclass]
        self.children = defaultdict(list)        # class -> [named subclass]
        self.annotations = defaultdict(list)     # entity -> [(prop, value, Axiom)]
        self.class_expr_axioms = defaultdict(list)   # class -> [Axiom] (defs/restrictions)
        self.refs = defaultdict(list)            # name -> [Axiom] referencing it

        for ax in self.doc.axioms():
            node = ax.node
            f = node.functor

            if f == "Declaration" and node.args:
                inner = node.args[0]
                if isinstance(inner, Node) and inner.args and isinstance(inner.args[0], Iri):
                    name = inner.args[0].value
                    self.dup_declared[name] += 1
                    if inner.functor in self.entities:
                        self.entities[inner.functor].add(name)
                        self.declared.setdefault(name, inner.functor)

            elif f == "SubClassOf" and len(node.args) == 2:
                sub, sup = node.args
                if isinstance(sub, Iri) and isinstance(sup, Iri):
                    self.parents[sub.value].append(sup.value)
                    self.children[sup.value].append(sub.value)
                elif isinstance(sub, Iri):
                    self.class_expr_axioms[sub.value].append(ax)

            elif f == "EquivalentClasses":
                for arg in node.args:
                    if isinstance(arg, Iri):
                        self.class_expr_axioms[arg.value].append(ax)
                        break

            elif f == "AnnotationAssertion" and len(node.args) >= 3:
                prop, subject, value = node.args[0], node.args[1], node.args[2]
                if isinstance(prop, Iri) and isinstance(subject, Iri):
                    self.annotations[subject.value].append((prop.value, value, ax))

            # reverse reference index
            seen = set()
            for iri in ofn.iter_iris(node):
                if iri.value not in seen:
                    seen.add(iri.value)
                    self.refs[iri.value].append(ax)

        # Annotation properties and datatypes that are *referenced* but not
        # necessarily declared - rdfs:label, xsd:integer, etc.
        self.referenced_annotation_props = set()
        self.referenced_datatypes = set()
        for ax in self.doc.axioms():
            f, A = ax.node.functor, ax.node.args
            if f == "AnnotationAssertion" and A and isinstance(A[0], Iri):
                self.referenced_annotation_props.add(A[0].value)
            elif f in ("AnnotationPropertyDomain",
                       "AnnotationPropertyRange") \
                    and A and isinstance(A[0], Iri):
                self.referenced_annotation_props.add(A[0].value)
            elif f == "SubAnnotationPropertyOf":
                for a in A:
                    if isinstance(a, Iri):
                        self.referenced_annotation_props.add(a.value)
            elif f == "DataPropertyRange" and len(A) >= 2 \
                    and isinstance(A[1], Iri):
                self.referenced_datatypes.add(A[1].value)
            for nd in iter_nodes(ax.node):
                if nd.functor in ("DataSomeValuesFrom",
                                  "DataAllValuesFrom") \
                        and len(nd.args) >= 2 \
                        and isinstance(nd.args[1], Iri):
                    self.referenced_datatypes.add(nd.args[1].value)
                for arg in nd.args:
                    if isinstance(arg, Literal) and arg.datatype is not None \
                            and not arg.datatype.is_full:
                        self.referenced_datatypes.add(arg.datatype.value)
        # remove anything that turned out to be declared as a different kind
        for q in list(self.referenced_annotation_props):
            if q in self.declared and self.declared[q] != "AnnotationProperty":
                self.referenced_annotation_props.discard(q)
        for q in list(self.referenced_datatypes):
            if q in self.declared and self.declared[q] != "Datatype":
                self.referenced_datatypes.discard(q)

        # Detect the ontology's preferred prefix from its own declarations,
        # so creating new entities works on any ontology, not just road signs.
        pcount = defaultdict(int)
        for name in self.declared:
            pfx = ofn.prefix_of(name)
            if pfx:
                pcount[pfx] += 1
        if pcount:
            self.default_prefix = max(pcount.items(), key=lambda kv: kv[1])[0]
        elif "" in self.doc.prefixes:
            self.default_prefix = ""
        else:
            self.default_prefix = ""

    # -- read-side accessors ------------------------------------------------

    def classes(self):
        return sorted(self.entities["Class"])

    def object_properties(self):
        return sorted(self.entities["ObjectProperty"])

    def data_properties(self):
        return sorted(self.entities["DataProperty"])

    def individuals(self):
        return sorted(self.entities["NamedIndividual"])

    def annotation_properties(self):
        """Declared annotation properties + any referenced in axioms
        (e.g. rdfs:label, rdfs:comment)."""
        return sorted(self.entities["AnnotationProperty"]
                       | self.referenced_annotation_props)

    def datatypes(self):
        """Declared datatypes + any referenced in literals or data
        property ranges (e.g. xsd:integer, xsd:decimal)."""
        return sorted(self.entities["Datatype"] | self.referenced_datatypes)

    def roots(self):
        """Classes with no asserted named superclass."""
        return sorted(c for c in self.entities["Class"] if not self.parents.get(c))

    def kind_of(self, name):
        q = self._qname(name)
        if q in self.declared:
            return self.declared[q]
        if q in self.referenced_annotation_props:
            return "AnnotationProperty"
        if q in self.referenced_datatypes:
            return "Datatype"
        return None

    def get_label(self, name):
        """First rdfs:label literal for an entity, or '' if none."""
        for prop, value, _ax in self.annotations.get(name, []):
            if prop in ("rdfs:label", "label") and isinstance(value, Literal):
                return value.lexical
        return ""

    def get_annotations(self, name):
        """List of (prop, text) annotation pairs for an entity."""
        out = []
        for prop, value, _ax in self.annotations.get(name, []):
            text = value.lexical if isinstance(value, Literal) else value.render()
            out.append((prop, text))
        return out

    def descendants(self, name):
        """Set of all transitive subclasses of `name`."""
        out = set()
        stack = list(self.children.get(name, []))
        while stack:
            cur = stack.pop()
            if cur not in out:
                out.add(cur)
                stack.extend(self.children.get(cur, []))
        return out

    def ancestors(self, name):
        """Set of all transitive superclasses of `name`."""
        out = set()
        stack = list(self.parents.get(name, []))
        while stack:
            cur = stack.pop()
            if cur not in out:
                out.add(cur)
                stack.extend(self.parents.get(cur, []))
        return out

    def referencing_axioms(self, name):
        """Source text of every axiom that mentions `name`."""
        return [ax.render() for ax in self.refs.get(self._qname(name), [])]

    def class_definitions(self, name):
        """Source text of every restriction / equivalence axiom on `name`."""
        return [ax.render() for ax in self.class_expr_axioms.get(self._qname(name), [])]

    def restriction_targets(self, name):
        """Object restrictions on a class as (property_qname, filler_qname).

        Covers ObjectSomeValuesFrom / ObjectAllValuesFrom / ObjectHasValue
        found anywhere in the class's SubClassOf / EquivalentClasses axioms."""
        name = self._qname(name)
        out = []
        for ax in self.class_expr_axioms.get(name, []):
            for nd in iter_nodes(ax.node):
                if nd.functor in ("ObjectSomeValuesFrom", "ObjectAllValuesFrom",
                                  "ObjectHasValue") and len(nd.args) == 2:
                    prop, filler = nd.args
                    if isinstance(prop, Iri) and isinstance(filler, Iri):
                        out.append((prop.value, filler.value))
        return out

    # -- structured relationship description -------------------------------

    def describe(self, name):
        """Describe an entity's relationships, Protege-style.

        Returns (kind, sections) where `sections` is an ordered list of
        (title, rows) and each row is a (display_text, target) tuple.
        `target` is a navigable qname, or None for class expressions and
        literals."""
        name = self._qname(name)
        kind = self.kind_of(name)        # declared OR referenced built-ins
        axioms = self.refs.get(name, [])

        def operand(arg):
            if isinstance(arg, Iri):
                return (ofn.local_name(arg.value), arg.value)
            return (self._pretty(arg), None)

        def others(args):
            """Operands of an n-ary axiom other than `name` itself."""
            return [operand(a) for a in args
                    if not (isinstance(a, Iri) and a.value == name)]

        def involves(args):
            return any(isinstance(a, Iri) and a.value == name for a in args)

        sections = []

        if kind == "Class":
            equiv, supers, disjoint, instances = [], [], [], []
            for ax in axioms:
                nd, f, A = ax.node, ax.node.functor, ax.node.args
                if f == "EquivalentClasses" and involves(A):
                    equiv += others(A)
                elif f == "SubClassOf" and len(A) == 2 \
                        and isinstance(A[0], Iri) and A[0].value == name:
                    supers.append(operand(A[1]))
                elif f == "DisjointClasses" and involves(A):
                    disjoint += others(A)
                elif f == "ClassAssertion" and len(A) == 2 \
                        and isinstance(A[0], Iri) and A[0].value == name \
                        and isinstance(A[1], Iri):
                    instances.append((ofn.local_name(A[1].value), A[1].value))
            subclasses = [(ofn.local_name(c), c)
                          for c in sorted(self.children.get(name, []))]
            sections = [
                ("Equivalent to", equiv),
                ("SubClass of", supers),
                ("Disjoint with", disjoint),
                ("Subclasses", subclasses),
                ("Instances", instances),
            ]

        elif kind in ("ObjectProperty", "DataProperty"):
            is_obj = kind == "ObjectProperty"
            sub_f = "SubObjectPropertyOf" if is_obj else "SubDataPropertyOf"
            dom_f = "ObjectPropertyDomain" if is_obj else "DataPropertyDomain"
            ran_f = "ObjectPropertyRange" if is_obj else "DataPropertyRange"
            eq_f = ("EquivalentObjectProperties" if is_obj
                    else "EquivalentDataProperties")
            supers, domains, ranges = [], [], []
            chars, inverses, equiv = [], [], []
            for ax in axioms:
                f, A = ax.node.functor, ax.node.args
                first_is_name = (A and isinstance(A[0], Iri)
                                 and A[0].value == name)
                if f == sub_f and len(A) == 2 and first_is_name:
                    supers.append(operand(A[1]))
                elif f == dom_f and len(A) == 2 and first_is_name:
                    domains.append(operand(A[1]))
                elif f == ran_f and len(A) == 2 and first_is_name:
                    ranges.append(operand(A[1]))
                elif f in CHARACTERISTIC_LABELS and first_is_name:
                    chars.append((CHARACTERISTIC_LABELS[f], None))
                elif f == "InverseObjectProperties" and involves(A):
                    inverses += others(A)
                elif f == eq_f and involves(A):
                    equiv += others(A)
            sections = [
                ("Equivalent to", equiv),
                ("Sub-property of", supers),
                ("Domains", domains),
                ("Ranges", ranges),
                ("Characteristics", chars),
            ]
            if is_obj:
                sections.append(("Inverse of", inverses))

        elif kind == "AnnotationProperty":
            supers, domains, ranges = [], [], []
            usage_count = 0
            for ax in self.doc.axioms():
                f, A = ax.node.functor, ax.node.args
                if f == "AnnotationAssertion" and A \
                        and isinstance(A[0], Iri) and A[0].value == name:
                    usage_count += 1
                elif f == "SubAnnotationPropertyOf" and len(A) >= 2 \
                        and isinstance(A[0], Iri) and A[0].value == name:
                    supers.append(operand(A[1]))
                elif f == "AnnotationPropertyDomain" and len(A) >= 2 \
                        and isinstance(A[0], Iri) and A[0].value == name:
                    domains.append(operand(A[1]))
                elif f == "AnnotationPropertyRange" and len(A) >= 2 \
                        and isinstance(A[0], Iri) and A[0].value == name:
                    ranges.append(operand(A[1]))
            sections = [
                ("Sub-property of", supers),
                ("Domain", domains),
                ("Range", ranges),
                ("Used in annotation assertions",
                 [("%d annotation assertion(s) use this property"
                   % usage_count, None)] if usage_count else []),
            ]

        elif kind == "Datatype":
            range_of = []   # data properties whose range is this datatype
            literal_count = 0
            for ax in self.doc.axioms():
                f, A = ax.node.functor, ax.node.args
                if f == "DataPropertyRange" and len(A) >= 2 \
                        and isinstance(A[1], Iri) and A[1].value == name \
                        and isinstance(A[0], Iri):
                    range_of.append(operand(A[0]))
                for nd in iter_nodes(ax.node):
                    for arg in nd.args:
                        if isinstance(arg, Literal) \
                                and arg.datatype is not None \
                                and arg.datatype.value == name:
                            literal_count += 1
            sections = [
                ("Range of (data properties)", range_of),
                ("Literal usages",
                 [("%d literal(s) typed as this" % literal_count, None)]
                 if literal_count else []),
            ]

        elif kind == "NamedIndividual":
            types_, obj_assert, data_assert = [], [], []
            same, different = [], []
            for ax in axioms:
                f, A = ax.node.functor, ax.node.args
                if f == "ClassAssertion" and len(A) == 2 \
                        and isinstance(A[1], Iri) and A[1].value == name:
                    types_.append(operand(A[0]))
                elif f == "ObjectPropertyAssertion" and len(A) == 3 \
                        and isinstance(A[1], Iri) and A[1].value == name:
                    tgt = operand(A[2])
                    obj_assert.append(
                        ("%s  ->  %s" % (self._pretty(A[0]), tgt[0]), tgt[1]))
                elif f == "DataPropertyAssertion" and len(A) == 3 \
                        and isinstance(A[1], Iri) and A[1].value == name:
                    data_assert.append(
                        ("%s  ->  %s" % (self._pretty(A[0]),
                                         self._pretty(A[2])), None))
                elif f == "SameIndividual" and involves(A):
                    same += others(A)
                elif f == "DifferentIndividuals" and involves(A):
                    different += others(A)
            sections = [
                ("Types", types_),
                ("Object property assertions", obj_assert),
                ("Data property assertions", data_assert),
                ("Same as", same),
                ("Different from", different),
            ]

        return kind, sections

    def _pretty(self, node):
        """Render a class expression / term in readable Manchester-ish form."""
        if isinstance(node, Iri):
            return ofn.local_name(node.value)
        if isinstance(node, Literal):
            if node.datatype is not None:
                return '"%s" (%s)' % (node.lexical,
                                      ofn.local_name(node.datatype.value))
            return '"%s"' % node.lexical
        f, A = node.functor, node.args
        P = self._pretty

        def wrap(x):
            text = P(x)
            if isinstance(x, Node) and x.functor in (
                    "ObjectIntersectionOf", "ObjectUnionOf"):
                return "(%s)" % text
            return text

        if f in ("ObjectSomeValuesFrom", "DataSomeValuesFrom") and len(A) == 2:
            return "%s some %s" % (P(A[0]), wrap(A[1]))
        if f in ("ObjectAllValuesFrom", "DataAllValuesFrom") and len(A) == 2:
            return "%s only %s" % (P(A[0]), wrap(A[1]))
        if f in ("ObjectHasValue", "DataHasValue") and len(A) == 2:
            return "%s value %s" % (P(A[0]), P(A[1]))
        if f == "ObjectIntersectionOf" and A:
            return " and ".join(wrap(x) for x in A)
        if f == "ObjectUnionOf" and A:
            return " or ".join(wrap(x) for x in A)
        if f == "ObjectComplementOf" and A:
            return "not %s" % wrap(A[0])
        if f in ("ObjectMinCardinality", "ObjectMaxCardinality",
                 "ObjectExactCardinality") and len(A) >= 2:
            kw = {"ObjectMinCardinality": "min",
                  "ObjectMaxCardinality": "max",
                  "ObjectExactCardinality": "exactly"}[f]
            tail = " " + wrap(A[2]) if len(A) > 2 else ""
            return "%s %s %s%s" % (P(A[1]), kw, P(A[0]), tail)
        if f == "ObjectOneOf":
            return "{%s}" % ", ".join(P(x) for x in A)
        return "%s(%s)" % (f, " ".join(P(x) for x in A))

    # -- name handling ------------------------------------------------------

    def _qname(self, name):
        """Normalise a user-entered name to prefixed form, using the
        ontology's own preferred prefix (auto-detected from declarations)."""
        name = (name or "").strip()
        if not name:
            raise ModelError("The name is empty.")
        if ":" in name:
            return name
        pfx = getattr(self, "default_prefix", "")
        return ("%s:%s" % (pfx, name)) if pfx else (":" + name)

    def _check_new_name(self, name):
        """Validate a name for a brand-new entity."""
        if name in self.declared:
            raise ModelError("'%s' already exists in the ontology." % name)
        local = ofn.local_name(name)
        if not _NAME_RE.match(local):
            raise ModelError(
                "Invalid name '%s'.\nUse letters, digits, '_', '-' and '.'; "
                "it must start with a letter or '_'." % local)
        pfx = ofn.prefix_of(name)
        if pfx and pfx not in self.doc.prefixes:
            raise ModelError("Unknown prefix '%s:'." % pfx)

    def _require(self, name, kind=None):
        """Ensure an entity exists (optionally of a given kind); return it."""
        if name not in self.declared:
            raise ModelError("'%s' does not exist in the ontology." % name)
        if kind and self.declared[name] != kind:
            raise ModelError("'%s' is a %s, not a %s."
                             % (name, self.declared[name], kind))
        return name

    # -- undo / redo --------------------------------------------------------

    def _begin(self):
        """Snapshot the document before a mutation."""
        self._undo.append(copy.deepcopy(self.doc.items))
        if len(self._undo) > 100:
            self._undo.pop(0)
        self._redo.clear()

    def _commit(self):
        self.reindex()
        self.dirty = True

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(self.doc.items)
        self.doc.items = self._undo.pop()
        self.reindex()
        self.dirty = True
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(self.doc.items)
        self.doc.items = self._redo.pop()
        self.reindex()
        self.dirty = True
        return True

    # -- item insertion / removal ------------------------------------------

    def _insert_axiom(self, node, after_functors):
        """Insert a new axiom after the last existing axiom of a matching
        functor (keeping the file tidily grouped), else append at the end."""
        ax = Axiom(node, dirty=True)
        items = self.doc.items
        anchor = -1
        for k, it in enumerate(items):
            if isinstance(it, Axiom) and it.functor in after_functors:
                anchor = k
        if anchor == -1:
            if items and not isinstance(items[-1], Trivia):
                items.append(Trivia("\n"))
            items.append(ax)
            items.append(Trivia("\n"))
            return ax
        at = anchor + 1
        if at < len(items) and isinstance(items[at], Trivia):
            at += 1
        items.insert(at, ax)
        items.insert(at + 1, Trivia("\n"))
        return ax

    def _remove_axioms(self, predicate):
        """Remove every Axiom matching `predicate` (and one trailing Trivia
        each, to avoid leaving blank gaps). Returns the count removed."""
        kept = []
        items = self.doc.items
        i = 0
        removed = 0
        while i < len(items):
            it = items[i]
            if isinstance(it, Axiom) and predicate(it):
                removed += 1
                i += 1
                if i < len(items) and isinstance(items[i], Trivia):
                    i += 1
                continue
            kept.append(it)
            i += 1
        self.doc.items = kept
        return removed

    @staticmethod
    def _is_named_subclassof(node, sub):
        """True when `node` is SubClassOf(sub, <named class>)."""
        return (node.functor == "SubClassOf" and len(node.args) == 2
                and isinstance(node.args[0], Iri) and node.args[0].value == sub
                and isinstance(node.args[1], Iri))

    @staticmethod
    def _references(node, name):
        """True when `name` appears anywhere inside `node`."""
        return any(iri.value == name for iri in ofn.iter_iris(node))

    # -- class operations ---------------------------------------------------

    def create_class(self, name, parents=(), label=None, comment=None):
        """Declare a new class, optionally with parents, a label and a comment."""
        name = self._qname(name)
        self._check_new_name(name)
        parents = [self._qname(p) for p in parents if str(p).strip()]
        for p in parents:
            self._require(p, "Class")
        self._begin()
        self._insert_axiom(_decl("Class", name), {"Declaration"})
        for p in parents:
            self._insert_axiom(_subclass(name, p), {"SubClassOf"})
        if label and label.strip():
            self._insert_axiom(_annotation("rdfs:label", name, label.strip()),
                               {"AnnotationAssertion"})
        if comment and comment.strip():
            self._insert_axiom(_annotation("rdfs:comment", name, comment.strip()),
                               {"AnnotationAssertion"})
        self._commit()
        return name

    def move_class(self, name, new_parents):
        """Replace a class's named superclasses with `new_parents`.

        Restriction / equivalence axioms on the class are left untouched."""
        name = self._qname(name)
        self._require(name, "Class")
        new_parents = [self._qname(p) for p in new_parents if str(p).strip()]
        for p in new_parents:
            self._require(p, "Class")
            if p == name:
                raise ModelError("A class cannot be its own parent.")
            if p in self.descendants(name):
                raise ModelError(
                    "'%s' is already a subclass of '%s' - that move would "
                    "create a cycle." % (ofn.local_name(p), ofn.local_name(name)))
        self._begin()
        self._remove_axioms(lambda ax: self._is_named_subclassof(ax.node, name))
        for p in new_parents:
            self._insert_axiom(_subclass(name, p), {"SubClassOf"})
        self._commit()

    def add_parent(self, name, parent):
        """Add one extra named superclass to a class (multiple inheritance)."""
        name = self._qname(name)
        parent = self._qname(parent)
        self._require(name, "Class")
        self._require(parent, "Class")
        if parent == name:
            raise ModelError("A class cannot be its own parent.")
        if parent in self.parents.get(name, []):
            raise ModelError("'%s' is already a parent of '%s'."
                             % (ofn.local_name(parent), ofn.local_name(name)))
        if parent in self.descendants(name):
            raise ModelError("That would create a cycle in the hierarchy.")
        self._begin()
        self._insert_axiom(_subclass(name, parent), {"SubClassOf"})
        self._commit()

    def remove_parent(self, name, parent):
        """Remove one named superclass edge."""
        name = self._qname(name)
        parent = self._qname(parent)
        self._require(name, "Class")
        if parent not in self.parents.get(name, []):
            raise ModelError("'%s' is not a parent of '%s'."
                             % (ofn.local_name(parent), ofn.local_name(name)))
        self._begin()
        self._remove_axioms(
            lambda ax: (ax.node.functor == "SubClassOf"
                        and len(ax.node.args) == 2
                        and isinstance(ax.node.args[0], Iri)
                        and ax.node.args[0].value == name
                        and isinstance(ax.node.args[1], Iri)
                        and ax.node.args[1].value == parent))
        self._commit()

    def delete_class(self, name, mode="reparent"):
        """Delete a class.

        mode = 'block'    -> refuse if the class has subclasses;
               'reparent' -> attach subclasses to the deleted class's parents;
               'subtree'  -> delete the class and all its descendants.
        Returns the number of axioms removed."""
        name = self._qname(name)
        self._require(name, "Class")
        kids = list(self.children.get(name, []))
        grandparents = list(self.parents.get(name, []))
        if mode == "block" and kids:
            raise ModelError(
                "'%s' has %d subclass(es); choose 'reparent' or 'subtree' "
                "deletion instead." % (ofn.local_name(name), len(kids)))
        self._begin()
        if mode == "subtree":
            targets = self.descendants(name) | {name}
            removed = self._remove_axioms(
                lambda ax: any(iri.value in targets
                               for iri in ofn.iter_iris(ax.node)))
        else:  # 'reparent' or 'block' (no kids)
            removed = self._remove_axioms(
                lambda ax: self._references(ax.node, name))
            existing = self.parents  # pre-deletion index
            for child in kids:
                for gp in grandparents:
                    if gp != child and gp not in existing.get(child, []):
                        self._insert_axiom(_subclass(child, gp), {"SubClassOf"})
        self._commit()
        return removed

    def rename_entity(self, old, new):
        """Rename an entity, rewriting the IRI in every axiom that uses it."""
        old = self._qname(old)
        new = self._qname(new)
        self._require(old)
        if old == new:
            raise ModelError("The new name is the same as the old one.")
        self._check_new_name(new)
        self._begin()
        for ax in self.doc.axioms():
            changed = False
            for iri in ofn.iter_iris(ax.node):
                if iri.value == old and not iri.is_full:
                    iri.value = new
                    changed = True
            if changed:
                ax.touch()
        self._commit()

    # -- annotations --------------------------------------------------------

    def set_annotation(self, entity, prop, text, lang="en"):
        """Create / update / clear an annotation (rdfs:label, rdfs:comment...).

        An empty `text` removes the annotation."""
        entity = self._qname(entity)
        self._require(entity)
        if ":" not in prop:
            prop = "rdfs:" + prop
        found = None
        for ax in self.doc.axioms():
            nd = ax.node
            if (nd.functor == "AnnotationAssertion" and len(nd.args) >= 3
                    and isinstance(nd.args[0], Iri) and nd.args[0].value == prop
                    and isinstance(nd.args[1], Iri) and nd.args[1].value == entity):
                found = ax
                break
        text = (text or "").strip()
        self._begin()
        if not text:
            if found is not None:
                self._remove_axioms(lambda ax: ax is found)
        elif found is not None:
            found.node.args[2] = Literal(text, lang or None)
            found.touch()
        else:
            self._insert_axiom(_annotation(prop, entity, text, lang),
                               {"AnnotationAssertion"})
        self._commit()

    # -- properties ---------------------------------------------------------

    def create_object_property(self, name, domain=None, range_=None,
                                characteristics=(), label=None, comment=None):
        """Declare a new object property with optional domain/range/traits."""
        name = self._qname(name)
        self._check_new_name(name)
        domain = self._qname(domain) if domain and domain.strip() else None
        range_ = self._qname(range_) if range_ and range_.strip() else None
        if domain:
            self._require(domain, "Class")
        if range_:
            self._require(range_, "Class")
        for ch in characteristics:
            if ch not in OBJECT_CHARACTERISTICS:
                raise ModelError("Unknown property characteristic '%s'." % ch)
        self._begin()
        self._insert_axiom(_decl("ObjectProperty", name), {"Declaration"})
        if domain:
            self._insert_axiom(Node("ObjectPropertyDomain",
                                    [Iri(name), Iri(domain)]),
                               {"ObjectPropertyDomain", "Declaration"})
        if range_:
            self._insert_axiom(Node("ObjectPropertyRange",
                                    [Iri(name), Iri(range_)]),
                               {"ObjectPropertyRange", "Declaration"})
        for ch in characteristics:
            self._insert_axiom(Node(ch, [Iri(name)]), {ch, "Declaration"})
        if label and label.strip():
            self._insert_axiom(_annotation("rdfs:label", name, label.strip()),
                               {"AnnotationAssertion"})
        if comment and comment.strip():
            self._insert_axiom(_annotation("rdfs:comment", name, comment.strip()),
                               {"AnnotationAssertion"})
        self._commit()
        return name

    def create_data_property(self, name, domain=None, range_="xsd:string",
                             functional=False, label=None, comment=None):
        """Declare a new data property with optional domain/range."""
        name = self._qname(name)
        self._check_new_name(name)
        domain = self._qname(domain) if domain and domain.strip() else None
        if domain:
            self._require(domain, "Class")
        self._begin()
        self._insert_axiom(_decl("DataProperty", name), {"Declaration"})
        if domain:
            self._insert_axiom(Node("DataPropertyDomain",
                                    [Iri(name), Iri(domain)]),
                               {"DataPropertyDomain", "Declaration"})
        if range_ and range_.strip():
            self._insert_axiom(Node("DataPropertyRange",
                                    [Iri(name), Iri(range_.strip())]),
                               {"DataPropertyRange", "Declaration"})
        if functional:
            self._insert_axiom(Node("FunctionalDataProperty", [Iri(name)]),
                               {"FunctionalDataProperty", "Declaration"})
        if label and label.strip():
            self._insert_axiom(_annotation("rdfs:label", name, label.strip()),
                               {"AnnotationAssertion"})
        if comment and comment.strip():
            self._insert_axiom(_annotation("rdfs:comment", name, comment.strip()),
                               {"AnnotationAssertion"})
        self._commit()
        return name

    # -- individuals --------------------------------------------------------

    def create_individual(self, name, types=()):
        """Declare a named individual, optionally asserting class membership."""
        name = self._qname(name)
        self._check_new_name(name)
        types = [self._qname(t) for t in types if str(t).strip()]
        for t in types:
            self._require(t, "Class")
        self._begin()
        self._insert_axiom(_decl("NamedIndividual", name), {"Declaration"})
        for t in types:
            self._insert_axiom(Node("ClassAssertion", [Iri(t), Iri(name)]),
                               {"ClassAssertion", "Declaration"})
        self._commit()
        return name

    # -- axiom-level operations --------------------------------------------

    # -- property assertions on individuals --------------------------------

    def add_object_property_assertion(self, individual, prop, target):
        """Assert  ObjectPropertyAssertion(prop, individual, target)."""
        individual = self._qname(individual)
        prop = self._qname(prop)
        target = self._qname(target)
        self._require(individual, "NamedIndividual")
        self._require(prop, "ObjectProperty")
        self._require(target, "NamedIndividual")
        self._begin()
        self._insert_axiom(
            Node("ObjectPropertyAssertion",
                 [Iri(prop), Iri(individual), Iri(target)]),
            {"ObjectPropertyAssertion", "ClassAssertion", "Declaration"})
        self._commit()

    def add_data_property_assertion(self, individual, prop, value,
                                     datatype=None):
        """Assert  DataPropertyAssertion(prop, individual, "value"^^datatype)."""
        individual = self._qname(individual)
        prop = self._qname(prop)
        self._require(individual, "NamedIndividual")
        self._require(prop, "DataProperty")
        text = str(value)
        lit_datatype = None
        if datatype and datatype.strip():
            lit_datatype = Iri(datatype.strip())
        self._begin()
        self._insert_axiom(
            Node("DataPropertyAssertion",
                 [Iri(prop), Iri(individual), Literal(text, None, lit_datatype)]),
            {"DataPropertyAssertion", "ClassAssertion", "Declaration"})
        self._commit()

    def add_same_individuals(self, names):
        """Assert SameIndividual(...) over two or more individuals."""
        names = [self._qname(n) for n in names if str(n).strip()]
        if len(names) < 2:
            raise ModelError("SameIndividual needs at least two individuals.")
        for n in names:
            self._require(n, "NamedIndividual")
        self._begin()
        self._insert_axiom(
            Node("SameIndividual", [Iri(n) for n in names]),
            {"SameIndividual", "DifferentIndividuals", "ClassAssertion"})
        self._commit()

    def add_different_individuals(self, names):
        """Assert DifferentIndividuals(...) over two or more individuals."""
        names = [self._qname(n) for n in names if str(n).strip()]
        if len(names) < 2:
            raise ModelError("DifferentIndividuals needs at least two "
                             "individuals.")
        for n in names:
            self._require(n, "NamedIndividual")
        self._begin()
        self._insert_axiom(
            Node("DifferentIndividuals", [Iri(n) for n in names]),
            {"DifferentIndividuals", "SameIndividual", "ClassAssertion"})
        self._commit()

    def add_class_assertion(self, individual, type_class):
        """Assert that `individual` is an instance of `type_class`."""
        individual = self._qname(individual)
        type_class = self._qname(type_class)
        self._require(individual, "NamedIndividual")
        self._require(type_class, "Class")
        self._begin()
        self._insert_axiom(
            Node("ClassAssertion", [Iri(type_class), Iri(individual)]),
            {"ClassAssertion", "Declaration"})
        self._commit()

    # ---------------------------------------------------------------------

    def add_disjoint_classes(self, names):
        """Assert that a group of classes are pairwise disjoint."""
        names = [self._qname(n) for n in names if str(n).strip()]
        if len(names) < 2:
            raise ModelError("Disjointness needs at least two classes.")
        for n in names:
            self._require(n, "Class")
        self._begin()
        self._insert_axiom(Node("DisjointClasses", [Iri(n) for n in names]),
                           {"DisjointClasses", "SubClassOf"})
        self._commit()

    def add_equivalent_classes(self, name_a, name_b):
        """Assert two classes equivalent."""
        a = self._qname(name_a)
        b = self._qname(name_b)
        self._require(a, "Class")
        self._require(b, "Class")
        self._begin()
        self._insert_axiom(Node("EquivalentClasses", [Iri(a), Iri(b)]),
                           {"EquivalentClasses", "SubClassOf"})
        self._commit()

    def add_restriction(self, cls, prop, filler, kind):
        """Add a SubClassOf restriction axiom to a class.

        kind = 'some'  -> SubClassOf(cls ObjectSomeValuesFrom(prop filler))
               'only'  -> SubClassOf(cls ObjectAllValuesFrom(prop filler))
               'value' -> SubClassOf(cls ObjectHasValue(prop filler))   (filler = individual)
               'data'  -> SubClassOf(cls DataHasValue(prop "filler"))   (filler = literal text)
        """
        cls = self._qname(cls)
        prop = self._qname(prop)
        self._require(cls, "Class")
        if kind == "data":
            self._require(prop, "DataProperty")
            restriction = Node("DataHasValue", [Iri(prop), Literal(str(filler))])
        else:
            self._require(prop, "ObjectProperty")
            filler = self._qname(filler)
            self._require(filler)
            if kind == "some":
                restriction = Node("ObjectSomeValuesFrom",
                                    [Iri(prop), Iri(filler)])
            elif kind == "only":
                restriction = Node("ObjectAllValuesFrom",
                                    [Iri(prop), Iri(filler)])
            elif kind == "value":
                restriction = Node("ObjectHasValue", [Iri(prop), Iri(filler)])
            else:
                raise ModelError("Unknown restriction kind '%s'." % kind)
        self._begin()
        self._insert_axiom(Node("SubClassOf", [Iri(cls), restriction]),
                           {"SubClassOf"})
        self._commit()

    def add_raw_axiom(self, text):
        """Parse and append an arbitrary functional-syntax axiom.

        The escape hatch for anything the dialogs do not cover."""
        text = (text or "").strip()
        if not text:
            raise ModelError("No axiom text supplied.")
        try:
            node = ofn.parse_axiom(text)
        except ofn.OfnError as exc:
            raise ModelError("Could not parse the axiom:\n%s" % exc)
        if node.functor not in ofn.AXIOM_KEYWORDS:
            raise ModelError(
                "'%s' is not a recognised OWL axiom keyword." % node.functor)
        self._begin()
        self._insert_axiom(node, {node.functor})
        self._commit()
        return node.functor

    def delete_axiom_text(self, raw_text):
        """Delete the first axiom whose rendered text equals `raw_text`."""
        target = raw_text.strip()
        match = None
        for ax in self.doc.axioms():
            if ax.render().strip() == target:
                match = ax
                break
        if match is None:
            raise ModelError("No axiom matches that text.")
        self._begin()
        self._remove_axioms(lambda ax: ax is match)
        self._commit()

    # -- validation ---------------------------------------------------------

    def validate(self):
        """Run structural checks. Returns a list of issue dicts:
        {'severity': 'error'|'warning'|'info', 'message': str, 'entity': str}."""
        issues = []

        # duplicate declarations
        for name, count in sorted(self.dup_declared.items()):
            if count > 1:
                issues.append({"severity": "warning", "entity": name,
                               "message": "'%s' is declared %d times."
                                          % (ofn.local_name(name), count)})

        # cycles in the class hierarchy
        for cyc in self._find_cycles():
            chain = " -> ".join(ofn.local_name(c) for c in cyc)
            issues.append({"severity": "error", "entity": cyc[0],
                           "message": "Cycle in class hierarchy: " + chain})

        # references to undeclared roadsigns: entities
        undeclared = defaultdict(int)
        for ax in self.doc.axioms():
            for iri in ofn.iter_iris(ax.node):
                v = iri.value
                if iri.is_full:
                    continue
                if ofn.prefix_of(v) == "roadsigns" and v not in self.declared:
                    undeclared[v] += 1
        for name, count in sorted(undeclared.items()):
            issues.append({"severity": "error", "entity": name,
                           "message": "'%s' is used in %d axiom(s) but never "
                                      "declared." % (ofn.local_name(name), count)})

        # self-referential / mutual subclass axioms are already covered by the
        # cycle check above; nothing extra needed here.

        # informational: classes with no parent (other than the natural roots)
        rootless = [c for c in self.entities["Class"]
                    if not self.parents.get(c)
                    and not self.class_expr_axioms.get(c)]
        if len(rootless) > 1:
            issues.append({
                "severity": "info", "entity": "",
                "message": "%d classes have no superclass: %s"
                           % (len(rootless),
                              ", ".join(ofn.local_name(c)
                                        for c in sorted(rootless)[:12]))
                           + (" ..." if len(rootless) > 12 else "")})

        return issues

    def _find_cycles(self):
        """Detect cycles among the named-superclass edges."""
        WHITE, GREY, BLACK = 0, 1, 2
        color = defaultdict(int)
        cycles = []

        def visit(node, stack):
            color[node] = GREY
            stack.append(node)
            for parent in self.parents.get(node, []):
                if color[parent] == GREY:
                    idx = stack.index(parent)
                    cycles.append(stack[idx:] + [parent])
                elif color[parent] == WHITE:
                    visit(parent, stack)
            stack.pop()
            color[node] = BLACK

        for cls in self.entities["Class"]:
            if color[cls] == WHITE:
                visit(cls, [])
        return cycles

    # -- statistics ---------------------------------------------------------

    def statistics(self):
        """A dict of headline counts for the Statistics dialog."""
        depth = self._max_depth()
        leaves = sum(1 for c in self.entities["Class"]
                     if not self.children.get(c))
        return {
            "Classes": len(self.entities["Class"]),
            "Object properties": len(self.entities["ObjectProperty"]),
            "Data properties": len(self.entities["DataProperty"]),
            "Named individuals": len(self.entities["NamedIndividual"]),
            "Total axioms": len(self.doc.axioms()),
            "Annotation assertions": sum(
                1 for a in self.doc.axioms()
                if a.functor == "AnnotationAssertion"),
            "Root classes": len(self.roots()),
            "Leaf classes": leaves,
            "Max hierarchy depth": depth,
        }

    def _max_depth(self):
        memo = {}

        def depth(node, seen):
            if node in memo:
                return memo[node]
            if node in seen:          # guard against cycles
                return 0
            seen = seen | {node}
            kids = self.children.get(node, [])
            d = 1 + max((depth(k, seen) for k in kids), default=0)
            memo[node] = d
            return d

        return max((depth(r, set()) for r in self.roots()), default=0)

    # -- saving -------------------------------------------------------------

    def save(self, path=None, make_backup=True):
        """Write the document back to disk.

        A timestamped `.bak` copy of the existing file is made first.
        Returns the backup path (or None when no backup was made)."""
        path = path or self.path
        if not path:
            raise ModelError("No file path given for saving.")
        backup = None
        if make_backup and os.path.exists(path):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = "%s.%s.bak" % (path, stamp)
            shutil.copy2(path, backup)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.doc.serialize())
        self.path = path
        self.dirty = False
        return backup

    def serialize(self):
        return self.doc.serialize()
