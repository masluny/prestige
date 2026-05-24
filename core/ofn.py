"""
ofn.py - OWL 2 Functional-Syntax engine.

A pure-Python tokenizer, parser, AST and minimal-diff serializer for the
subset of OWL 2 Functional Syntax used by Protege exports (such as the Vienna
Convention road-signs ontology this tool was built for).

Design goals
------------
* Lossless round-trip: an unmodified document re-serializes byte-for-byte.
* Minimal diff: only axioms that were actually edited get re-rendered; every
  untouched axiom is written back exactly as it was read.
* No third-party dependencies (standard library only).

Public API
----------
parse(text)        -> Document
load(path)         -> Document
Document.serialize() -> str
Document.axioms()  -> list[Axiom]
AST node types     : Node, Iri, Literal
Helpers            : local_name(), prefix_of(), AXIOM_KEYWORDS
"""

import re

__all__ = [
    "OfnError", "Iri", "Literal", "Node", "Trivia", "Axiom", "Document",
    "parse", "load", "tokenize", "parse_axiom", "parse_expr",
    "local_name", "prefix_of", "AXIOM_KEYWORDS",
]


class OfnError(Exception):
    """Raised when functional-syntax text cannot be tokenized or parsed."""


# ---------------------------------------------------------------------------
# AST node types
# ---------------------------------------------------------------------------

class Iri:
    """A reference to a class / property / individual / datatype.

    `value`   - the text of the reference:
                  * abbreviated:  'roadsigns:StopSign', 'rdfs:label'
                  * full:         'https://example.org/roadsigns'
    `is_full` - True when written as <...> in the source.
    """

    __slots__ = ("value", "is_full")

    def __init__(self, value, is_full=False):
        self.value = value
        self.is_full = is_full

    def render(self):
        return "<%s>" % self.value if self.is_full else self.value

    def __repr__(self):
        return "Iri(%r)" % self.value

    def __eq__(self, other):
        return (isinstance(other, Iri)
                and other.value == self.value
                and other.is_full == self.is_full)

    def __hash__(self):
        return hash((self.value, self.is_full))


class Literal:
    """A typed or language-tagged literal value."""

    __slots__ = ("lexical", "lang", "datatype")

    def __init__(self, lexical, lang=None, datatype=None):
        self.lexical = lexical          # the decoded text
        self.lang = lang                # e.g. 'en' or None
        self.datatype = datatype        # an Iri or None

    def render(self):
        esc = self.lexical.replace("\\", "\\\\").replace('"', '\\"')
        out = '"%s"' % esc
        if self.lang:
            out += "@" + self.lang
        elif self.datatype is not None:
            out += "^^" + self.datatype.render()
        return out

    def __repr__(self):
        return "Literal(%r)" % self.lexical

    def __eq__(self, other):
        return (isinstance(other, Literal)
                and other.lexical == self.lexical
                and other.lang == self.lang
                and other.datatype == self.datatype)

    def __hash__(self):
        return hash((self.lexical, self.lang))


class Node:
    """A functional expression: `functor(arg arg ...)`.

    Used uniformly for axioms (SubClassOf, Declaration, ...) and for nested
    class expressions (ObjectSomeValuesFrom, ObjectIntersectionOf, ...).
    """

    __slots__ = ("functor", "args")

    def __init__(self, functor, args=None):
        self.functor = functor
        self.args = list(args) if args is not None else []

    def render(self):
        return "%s(%s)" % (self.functor, " ".join(a.render() for a in self.args))

    def __repr__(self):
        return "Node(%r, %r)" % (self.functor, self.args)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_WS = " \t\r\n"
_NAME_STOP = set(' \t\r\n()"<>#')


def _read_string(s, i):
    """Read a double-quoted string starting at s[i] == '"'.

    Returns (decoded_text, index_just_past_closing_quote). Handles the OWL
    escapes \\\\ and \\" and tolerates literal newlines inside the string.
    """
    n = len(s)
    j = i + 1
    buf = []
    while j < n:
        c = s[j]
        if c == "\\" and j + 1 < n:
            buf.append(s[j + 1])
            j += 2
            continue
        if c == '"':
            return "".join(buf), j + 1
        buf.append(c)
        j += 1
    raise OfnError("unterminated string literal at offset %d" % i)


def tokenize(s):
    """Turn functional-syntax text into a flat list of (kind, value) tokens.

    kinds: 'L' '(' , 'R' ')' , 'NAME' , 'IRI' , 'LIT'.
    For 'LIT' the value is a tuple (lexical, lang_or_None, datatype_or_None);
    datatype is itself ('IRI', text) or ('NAME', text).
    Whitespace and `#` comments are discarded.
    """
    toks = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in _WS:
            i += 1
            continue
        if c == "#":
            while i < n and s[i] != "\n":
                i += 1
            continue
        if c == "(":
            toks.append(("L", "("))
            i += 1
            continue
        if c == ")":
            toks.append(("R", ")"))
            i += 1
            continue
        if c == "<":
            j = s.find(">", i)
            if j < 0:
                raise OfnError("unterminated IRI reference at offset %d" % i)
            toks.append(("IRI", s[i + 1:j]))
            i = j + 1
            continue
        if c == '"':
            lex, j = _read_string(s, i)
            lang = None
            dt = None
            if j < n and s[j] == "@":
                k = j + 1
                while k < n and (s[k].isalnum() or s[k] == "-"):
                    k += 1
                lang = s[j + 1:k]
                j = k
            elif j + 1 < n and s[j] == "^" and s[j + 1] == "^":
                j += 2
                if j < n and s[j] == "<":
                    k = s.find(">", j)
                    if k < 0:
                        raise OfnError("unterminated datatype IRI at %d" % j)
                    dt = ("IRI", s[j + 1:k])
                    j = k + 1
                else:
                    k = j
                    while k < n and s[k] not in _NAME_STOP:
                        k += 1
                    dt = ("NAME", s[j:k])
                    j = k
            toks.append(("LIT", (lex, lang, dt)))
            i = j
            continue
        # bare name: prefixed name or functor keyword
        j = i
        while j < n and s[j] not in _NAME_STOP:
            j += 1
        if j == i:
            raise OfnError("unexpected character %r at offset %d" % (c, i))
        toks.append(("NAME", s[i:j]))
        i = j
    return toks


# ---------------------------------------------------------------------------
# Parser (recursive descent)
# ---------------------------------------------------------------------------

def parse_expr(toks, pos):
    """Parse one expression from toks[pos:]. Returns (ast, next_pos)."""
    if pos >= len(toks):
        raise OfnError("unexpected end of input")
    kind, val = toks[pos]
    if kind == "IRI":
        return Iri(val, is_full=True), pos + 1
    if kind == "LIT":
        lex, lang, dt = val
        datatype = None
        if dt is not None:
            datatype = Iri(dt[1], is_full=(dt[0] == "IRI"))
        return Literal(lex, lang, datatype), pos + 1
    if kind == "NAME":
        if pos + 1 < len(toks) and toks[pos + 1][0] == "L":
            args = []
            p = pos + 2
            while p < len(toks) and toks[p][0] != "R":
                arg, p = parse_expr(toks, p)
                args.append(arg)
            if p >= len(toks):
                raise OfnError("missing ')' for %s(" % val)
            return Node(val, args), p + 1
        return Iri(val, is_full=False), pos + 1
    raise OfnError("unexpected token %r" % (toks[pos],))


def parse_axiom(text):
    """Parse a single functional-syntax axiom string into a Node."""
    toks = tokenize(text)
    if not toks:
        raise OfnError("empty axiom")
    ast, p = parse_expr(toks, 0)
    if p != len(toks):
        raise OfnError("trailing tokens after axiom")
    if not isinstance(ast, Node):
        raise OfnError("axiom must be a functional expression like Functor(...)")
    return ast


# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------

class Trivia:
    """Whitespace or a comment line between axioms - emitted verbatim."""

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def render(self):
        return self.text


class Axiom:
    """One top-level axiom.

    `raw`   - the exact source text (None for axioms created in-memory).
    `dirty` - when True the axiom was edited and is re-rendered from `node`;
              when False the original `raw` text is emitted unchanged.
    """

    __slots__ = ("node", "raw", "dirty")

    def __init__(self, node, raw=None, dirty=False):
        self.node = node
        self.raw = raw
        self.dirty = dirty or raw is None

    @property
    def functor(self):
        return self.node.functor

    def touch(self):
        """Mark the axiom as edited so it is re-rendered on save."""
        self.dirty = True

    def render(self):
        if self.dirty or self.raw is None:
            return self.node.render()
        return self.raw


class Document:
    """A parsed functional-syntax ontology document."""

    def __init__(self, prologue, items, epilogue, prefixes,
                 ontology_iri, version_iri):
        self.prologue = prologue          # verbatim text up to the first axiom
        self.items = items                # list[Trivia | Axiom]
        self.epilogue = epilogue          # verbatim closing ')' + trailing text
        self.prefixes = prefixes          # {prefix_name: namespace_iri}, '' = default
        self.ontology_iri = ontology_iri
        self.version_iri = version_iri

    def serialize(self):
        """Reconstruct the full document text."""
        parts = [self.prologue]
        for it in self.items:
            parts.append(it.render())
        parts.append(self.epilogue)
        return "".join(parts)

    def axioms(self):
        """All Axiom items in document order."""
        return [it for it in self.items if isinstance(it, Axiom)]


# ---------------------------------------------------------------------------
# Balanced-expression scanner + document splitter
# ---------------------------------------------------------------------------

def _scan_balanced(s, start):
    """Return the index just past the balanced expression beginning at `start`.

    Respects string literals, `#` comments and <...> IRI references so that
    parentheses inside them are not miscounted.
    """
    i, n = start, len(s)
    depth = 0
    opened = False
    while i < n:
        c = s[i]
        if c == '"':
            _, i = _read_string(s, i)
            continue
        if c == "<":
            j = s.find(">", i)
            if j < 0:
                raise OfnError("unterminated IRI reference at offset %d" % i)
            i = j + 1
            continue
        if c == "#":
            while i < n and s[i] != "\n":
                i += 1
            continue
        if c == "(":
            depth += 1
            opened = True
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            if opened and depth == 0:
                return i
            continue
        i += 1
    raise OfnError("unbalanced expression starting at offset %d" % start)


def _split_body(region):
    """Split the axiom region into an ordered list of Trivia / Axiom items."""
    items = []
    i, n = 0, len(region)
    while i < n:
        c = region[i]
        if c in _WS:
            j = i
            while j < n and region[j] in _WS:
                j += 1
            items.append(Trivia(region[i:j]))
            i = j
            continue
        if c == "#":
            j = i
            while j < n and region[j] != "\n":
                j += 1
            items.append(Trivia(region[i:j]))
            i = j
            continue
        j = _scan_balanced(region, i)
        raw = region[i:j]
        items.append(Axiom(parse_axiom(raw), raw))
        i = j
    return items


_PREFIX_RE = re.compile(r"Prefix\(\s*([^\s:=]*)\s*:=\s*<([^>]*)>\s*\)")
_ONTOLOGY_RE = re.compile(r"Ontology\s*\(")
_FUNCTOR_RE = re.compile(r"[^\s()\"<>#]+")


def parse(text):
    """Parse functional-syntax `text` into a Document.

    The document is split into three regions:
      * prologue  - prefixes, `Ontology(`, ontology/version IRIs and
                    ontology-level annotations (kept verbatim);
      * items     - the axioms and the comments/whitespace among them;
      * epilogue  - the closing `)` and any trailing text (kept verbatim).
    """
    prefixes = {}
    for m in _PREFIX_RE.finditer(text):
        prefixes[m.group(1)] = m.group(2)

    m = _ONTOLOGY_RE.search(text)
    if not m:
        raise OfnError("no Ontology(...) declaration found")

    p = m.end()
    n = len(text)
    ontology_iri = None
    version_iri = None
    ontology_annotations = []        # captured for the metadata viewer
    while p < n:
        c = text[p]
        if c in _WS:
            p += 1
            continue
        if c == "#":
            while p < n and text[p] != "\n":
                p += 1
            continue
        if c == "<":
            j = text.find(">", p)
            if j < 0:
                raise OfnError("unterminated IRI in ontology header")
            iri = text[p + 1:j]
            if ontology_iri is None:
                ontology_iri = iri
            elif version_iri is None:
                version_iri = iri
            p = j + 1
            continue
        if c == ")":
            break  # ontology with no axioms
        fm = _FUNCTOR_RE.match(text, p)
        if not fm:
            raise OfnError("unexpected character in ontology header at %d" % p)
        if fm.group(0) == "Annotation":
            end = _scan_balanced(text, p)
            try:
                ontology_annotations.append(parse_axiom(text[p:end]))
            except OfnError:
                pass  # tolerate odd ontology-level annotations
            p = end
            continue
        break  # reached the first real axiom

    prologue_end = p
    body_end = text.rfind(")")
    if body_end < prologue_end:
        body_end = prologue_end

    prologue = text[:prologue_end]
    region = text[prologue_end:body_end]
    epilogue = text[body_end:]
    items = _split_body(region)
    doc = Document(prologue, items, epilogue, prefixes,
                   ontology_iri, version_iri)
    doc.ontology_annotations = ontology_annotations
    return doc


def load(path):
    """Read and parse a functional-syntax file."""
    with open(path, "r", encoding="utf-8") as fh:
        return parse(fh.read())


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def local_name(name):
    """Local part of a (possibly prefixed) name: 'roadsigns:Stop' -> 'Stop'."""
    return name.split(":", 1)[1] if ":" in name else name


def prefix_of(name):
    """Prefix part of a (possibly prefixed) name: 'roadsigns:Stop' -> 'roadsigns'."""
    return name.split(":", 1)[0] if ":" in name else ""


def iter_iris(node):
    """Yield every Iri appearing anywhere inside an AST node (depth-first)."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, Iri):
            yield cur
        elif isinstance(cur, Literal):
            if cur.datatype is not None:
                yield cur.datatype
        elif isinstance(cur, Node):
            stack.extend(cur.args)


# Recognised OWL 2 axiom functors - used to validate raw-axiom input.
AXIOM_KEYWORDS = frozenset([
    "Declaration", "SubClassOf", "EquivalentClasses", "DisjointClasses",
    "DisjointUnion", "SubObjectPropertyOf", "EquivalentObjectProperties",
    "DisjointObjectProperties", "InverseObjectProperties",
    "ObjectPropertyDomain", "ObjectPropertyRange", "FunctionalObjectProperty",
    "InverseFunctionalObjectProperty", "ReflexiveObjectProperty",
    "IrreflexiveObjectProperty", "SymmetricObjectProperty",
    "AsymmetricObjectProperty", "TransitiveObjectProperty",
    "SubDataPropertyOf", "EquivalentDataProperties", "DisjointDataProperties",
    "DataPropertyDomain", "DataPropertyRange", "FunctionalDataProperty",
    "DatatypeDefinition", "HasKey", "SameIndividual", "DifferentIndividuals",
    "ClassAssertion", "ObjectPropertyAssertion",
    "NegativeObjectPropertyAssertion", "DataPropertyAssertion",
    "NegativeDataPropertyAssertion", "AnnotationAssertion",
    "SubAnnotationPropertyOf", "AnnotationPropertyDomain",
    "AnnotationPropertyRange",
])
