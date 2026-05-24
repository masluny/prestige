"""
reasoner.py - optional OWL reasoning bridge.

The editor itself is pure-Python and works without this module. When the
`owlready2` package and a Java runtime are available, this bridge:

  * translates the in-memory ontology model into an `owlready2` ontology;
  * runs the HermiT reasoner to check logical consistency and find
    unsatisfiable classes and inferred subclass relations;
  * exports the ontology to RDF/XML (this part needs only owlready2, no Java).

Every axiom is translated inside a try/except: anything that cannot be
expressed is skipped and counted, so reasoning never crashes the editor.

`owlready2` is imported lazily, so `import reasoner` always succeeds even when
the package is absent.
"""

import shutil
import subprocess
import types

from . import ofn


class ReasonerUnavailable(Exception):
    """Raised when owlready2 (or Java) is needed but not present."""


class _Skip(Exception):
    """Internal: an axiom or expression that cannot be translated."""


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def _import_owlready():
    try:
        import owlready2
        return owlready2
    except Exception:
        raise ReasonerUnavailable(
            "owlready2 is not installed.\n"
            "Install it with:  python3 -m pip install owlready2")


def _java_works():
    """True only when `java` is present AND actually runnable.

    macOS ships a `/usr/bin/java` stub that exists on PATH but exits with an
    error until a real JDK is installed - so existence alone is not enough.
    """
    java = shutil.which("java")
    if not java:
        return False
    try:
        result = subprocess.run([java, "-version"],
                                capture_output=True, timeout=15)
        return result.returncode == 0
    except Exception:
        return False


def check_available():
    """Return (ok, message) describing whether reasoning can run."""
    try:
        import owlready2  # noqa: F401
    except Exception:
        return False, ("owlready2 is not installed. RDF/XML export and "
                       "reasoning are disabled.\n"
                       "Install it with:  python3 -m pip install owlready2")
    if not _java_works():
        return False, ("owlready2 is installed, but no working Java runtime "
                        "was found - HermiT needs Java.\n"
                        "Install one, e.g.:  brew install --cask temurin\n"
                        "(RDF/XML export still works without Java.)")
    return True, "owlready2 and Java are available - ready to reason."


# ---------------------------------------------------------------------------
# Value / datatype helpers
# ---------------------------------------------------------------------------

_DATATYPES = {
    "xsd:string": str, "xsd:integer": int, "xsd:int": int, "xsd:long": int,
    "xsd:decimal": float, "xsd:float": float, "xsd:double": float,
    "xsd:boolean": bool, "xsd:nonNegativeInteger": int,
    "xsd:positiveInteger": int, "xsd:dateTime": str,
}


def _literal_value(lit):
    """Convert an ofn.Literal into a plain Python value."""
    dt = lit.datatype.value if lit.datatype is not None else None
    if dt in ("xsd:integer", "xsd:int", "xsd:long", "xsd:nonNegativeInteger",
              "xsd:positiveInteger"):
        return int(lit.lexical)
    if dt in ("xsd:decimal", "xsd:float", "xsd:double"):
        return float(lit.lexical)
    if dt == "xsd:boolean":
        return lit.lexical.strip().lower() in ("true", "1")
    return lit.lexical


def _datatype(node):
    if isinstance(node, ofn.Iri):
        return _DATATYPES.get(node.value, str)
    return str


def _safe_name(qname):
    """The IRI-fragment name for an entity (its local part)."""
    return ofn.local_name(qname)


# ---------------------------------------------------------------------------
# Translation: ofn model -> owlready2 ontology
# ---------------------------------------------------------------------------

class _Translator:

    def __init__(self, model):
        self.owl = _import_owlready()
        self.model = model
        self.ents = {}          # qname -> owlready2 object
        self.skipped = 0
        self.world = self.owl.World()
        base = (model.doc.ontology_iri or "https://example.org/roadsigns")
        if not base.endswith(("#", "/")):
            base += "#"
        self.onto = self.world.get_ontology(base)

    # -- entity / expression resolution ------------------------------------

    def _entity(self, iri):
        if iri.value in self.ents:
            return self.ents[iri.value]
        raise _Skip("unknown entity %s" % iri.value)

    def _expr(self, node):
        """Translate a class expression / term into an owlready2 construct."""
        owl = self.owl
        if isinstance(node, ofn.Iri):
            return self._entity(node)
        if isinstance(node, ofn.Literal):
            return _literal_value(node)

        f, a = node.functor, node.args
        if f == "ObjectIntersectionOf":
            parts = [self._expr(x) for x in a]
            result = parts[0]
            for p in parts[1:]:
                result = result & p
            return result
        if f == "ObjectUnionOf":
            parts = [self._expr(x) for x in a]
            result = parts[0]
            for p in parts[1:]:
                result = result | p
            return result
        if f == "ObjectComplementOf":
            return owl.Not(self._expr(a[0]))
        if f == "ObjectSomeValuesFrom":
            return self._entity(a[0]).some(self._expr(a[1]))
        if f == "ObjectAllValuesFrom":
            return self._entity(a[0]).only(self._expr(a[1]))
        if f == "ObjectHasValue":
            return self._entity(a[0]).value(self._expr(a[1]))
        if f == "DataHasValue":
            return self._entity(a[0]).value(_literal_value(a[1]))
        if f == "DataSomeValuesFrom":
            return self._entity(a[0]).some(_datatype(a[1]))
        if f == "DataAllValuesFrom":
            return self._entity(a[0]).only(_datatype(a[1]))
        if f in ("ObjectMinCardinality", "ObjectMaxCardinality",
                 "ObjectExactCardinality"):
            n = int(a[0].value)
            prop = self._entity(a[1])
            filler = self._expr(a[2]) if len(a) > 2 else None
            method = {"ObjectMinCardinality": prop.min,
                      "ObjectMaxCardinality": prop.max,
                      "ObjectExactCardinality": prop.exactly}[f]
            return method(n, filler) if filler is not None else method(n)
        if f == "ObjectOneOf":
            return owl.OneOf([self._expr(x) for x in a])
        raise _Skip("unsupported expression %s" % f)

    # -- building -----------------------------------------------------------

    def build(self):
        """Create all entities and translate every axiom."""
        owl = self.owl
        m = self.model
        Thing = owl.Thing
        ObjectProperty = owl.ObjectProperty
        DataProperty = owl.DataProperty

        with self.onto:
            for q in sorted(m.entities["Class"]):
                self.ents[q] = types.new_class(_safe_name(q), (Thing,))
            for q in sorted(m.entities["ObjectProperty"]):
                self.ents[q] = types.new_class(_safe_name(q),
                                               (ObjectProperty,))
            for q in sorted(m.entities["DataProperty"]):
                self.ents[q] = types.new_class(_safe_name(q),
                                               (DataProperty,))
            for q in sorted(m.entities["NamedIndividual"]):
                try:
                    self.ents[q] = Thing(_safe_name(q))
                except Exception:
                    self.skipped += 1

            for ax in m.doc.axioms():
                try:
                    self._apply(ax.node)
                except _Skip:
                    self.skipped += 1
                except Exception:
                    self.skipped += 1

        return self.world, self.onto, self.ents, self.skipped

    _CHARACTERISTICS = {
        "FunctionalObjectProperty": "FunctionalProperty",
        "InverseFunctionalObjectProperty": "InverseFunctionalProperty",
        "TransitiveObjectProperty": "TransitiveProperty",
        "SymmetricObjectProperty": "SymmetricProperty",
        "AsymmetricObjectProperty": "AsymmetricProperty",
        "ReflexiveObjectProperty": "ReflexiveProperty",
        "IrreflexiveObjectProperty": "IrreflexiveProperty",
        "FunctionalDataProperty": "FunctionalProperty",
    }

    def _apply(self, node):
        """Translate one axiom into owlready2 statements."""
        owl = self.owl
        f, a = node.functor, node.args

        if f == "Declaration" or f.startswith("Annotation") \
                or f in ("HasKey", "DatatypeDefinition", "SameIndividual"):
            return

        if f == "SubClassOf":
            sub = self._expr(a[0])
            sub.is_a.append(self._expr(a[1]))
            return

        if f == "EquivalentClasses":
            head = self._expr(a[0])
            for other in a[1:]:
                head.equivalent_to.append(self._expr(other))
            return

        if f == "DisjointClasses":
            owl.AllDisjoint([self._expr(x) for x in a])
            return

        if f == "DifferentIndividuals":
            owl.AllDifferent([self._expr(x) for x in a])
            return

        if f in ("SubObjectPropertyOf", "SubDataPropertyOf"):
            self._entity(a[0]).is_a.append(self._entity(a[1]))
            return

        if f in ("ObjectPropertyDomain", "DataPropertyDomain"):
            self._entity(a[0]).domain.append(self._expr(a[1]))
            return

        if f == "ObjectPropertyRange":
            self._entity(a[0]).range.append(self._expr(a[1]))
            return

        if f == "DataPropertyRange":
            self._entity(a[0]).range.append(_datatype(a[1]))
            return

        if f in self._CHARACTERISTICS:
            trait = getattr(owl, self._CHARACTERISTICS[f])
            self._entity(a[0]).is_a.append(trait)
            return

        if f == "ClassAssertion":
            cls = self._expr(a[0])
            ind = self._entity(a[1])
            ind.is_a.append(cls)
            return

        if f == "ObjectPropertyAssertion":
            prop = self._entity(a[0])
            prop[self._entity(a[1])].append(self._entity(a[2]))
            return

        if f == "DataPropertyAssertion":
            prop = self._entity(a[0])
            prop[self._entity(a[1])].append(_literal_value(a[2]))
            return

        raise _Skip("unsupported axiom %s" % f)


def _qname_of(entity):
    """A roadsigns-prefixed name for an owlready2 entity, for display."""
    return "roadsigns:" + getattr(entity, "name", "?")


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------

def _run_with(model, sync_call, label):
    """Generic reasoner runner: translates the model, runs `sync_call(world)`,
    then reports consistency / unsatisfiable / inferred-subclass results."""
    owl = _import_owlready()
    if not _java_works():
        raise ReasonerUnavailable(
            "No working Java runtime found - %s needs Java.\n"
            "Install one, e.g.:  brew install --cask temurin" % label)

    world, onto, ents, skipped = _Translator(model).build()
    classes = [ents[q] for q in model.entities["Class"] if q in ents]

    before = {}
    for c in classes:
        try:
            before[c] = set(c.ancestors())
        except Exception:
            before[c] = set()

    consistent = True
    try:
        with onto:
            sync_call(world)
    except owl.OwlReadyInconsistentOntologyError:
        consistent = False
    except TypeError:
        # older owlready2 signatures take a list of ontologies
        with onto:
            try:
                sync_call([onto])
            except owl.OwlReadyInconsistentOntologyError:
                consistent = False

    unsatisfiable = []
    try:
        unsatisfiable = sorted(_qname_of(c)
                               for c in world.inconsistent_classes())
    except Exception:
        pass

    inferred = []
    if consistent:
        for c in classes:
            try:
                after = set(c.ancestors())
            except Exception:
                continue
            for anc in after - before.get(c, set()):
                if anc is c:
                    continue
                if getattr(anc, "name", None) in (None, "Thing"):
                    continue
                inferred.append((_qname_of(c), _qname_of(anc)))
        inferred.sort()

    return {
        "reasoner": label,
        "consistent": consistent,
        "unsatisfiable": unsatisfiable,
        "inferred_subclasses": inferred,
        "skipped": skipped,
    }


def run_reasoner_hermit(model):
    """Run HermiT on the model (needs owlready2 + Java)."""
    owl = _import_owlready()
    return _run_with(model,
        lambda w: owl.sync_reasoner_hermit(w, infer_property_values=False, debug=0),
        "HermiT")


def run_reasoner_pellet(model):
    """Run Pellet on the model (needs owlready2 + Java)."""
    owl = _import_owlready()
    return _run_with(model,
        lambda w: owl.sync_reasoner_pellet(w, infer_property_values=False,
                                           infer_data_property_values=False,
                                           debug=0),
        "Pellet")


# Backwards-compatible alias.
run_reasoner = run_reasoner_hermit


# ---------------------------------------------------------------------------
# BORN (Bayesian OWL Reasoner) - file creation + external execution
# ---------------------------------------------------------------------------

#: Annotation IRI used to attach probabilities to SubClassOf axioms.
BORN_PROBABILITY_IRI = "https://born.julianmendez.com/annotation#probability"


def create_born_file(model, out_path, default_probability="1.0"):
    """Write a BORN-style copy of the ontology to `out_path`.

    Every `SubClassOf(...)` axiom is wrapped with an axiom annotation that
    carries a probability literal, so the file is readable by tools such as
    BORN (Bayesian OWL Reasoner) that expect probabilities on subclass
    axioms. The original ontology is not modified.

    Returns (path, annotated_axiom_count).
    """
    import copy
    from . import ofn as _ofn

    doc = copy.deepcopy(model.doc)
    n_annotated = 0
    for item in doc.items:
        if isinstance(item, _ofn.Axiom) and item.node.functor == "SubClassOf" \
                and len(item.node.args) == 2:
            ann = _ofn.Node("Annotation", [
                _ofn.Iri(BORN_PROBABILITY_IRI, is_full=True),
                _ofn.Literal(str(default_probability),
                              datatype=_ofn.Iri("xsd:decimal")),
            ])
            item.node = _ofn.Node("SubClassOf",
                                  [ann] + list(item.node.args))
            item.dirty = True
            n_annotated += 1

    # Declare the probability annotation property at the top of the body.
    decl = _ofn.Node("Declaration", [
        _ofn.Node("AnnotationProperty",
                  [_ofn.Iri(BORN_PROBABILITY_IRI, is_full=True)])])
    doc.items.insert(0, _ofn.Axiom(decl, dirty=True))
    doc.items.insert(1, _ofn.Trivia("\n# BORN-style axioms with probability "
                                     "annotations follow.\n"))

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(doc.serialize())
    return out_path, n_annotated


def _find_born_jar():
    """Locate a born.jar via the BORN_JAR env var or common project locations."""
    import os
    env = os.environ.get("BORN_JAR")
    if env and os.path.isfile(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    project = os.path.dirname(here)
    candidates = [
        os.path.join(project, "born.jar"),
        os.path.join(project, "vendor", "born.jar"),
        os.path.expanduser("~/born.jar"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def run_born_builtin(born_owl_path):
    """Built-in probabilistic analyser for a BORN-formatted ontology.

    Loads the file and, for every SubClassOf axiom annotated with a
    probability literal (the format produced by `create_born_file`):
      * counts probabilistic vs deterministic axioms and their distribution;
      * propagates the probabilities along the named subclass chain by
        multiplying probabilities along each path - so for every (subclass,
        ancestor) pair we report the most-probable path's compound
        probability;
      * lists the most-uncertain inferences first (lowest compound
        probability), highlighting where the ontology's evidence is weakest.

    Pure Python - no external dependency required.
    """
    from collections import defaultdict
    from . import ofn

    doc = ofn.load(born_owl_path)

    edges = defaultdict(list)        # child_qname -> [(parent_qname, p)]
    n_subclass = n_prob = n_det = 0
    bins = {"=1.0": 0, "0.9-1.0": 0, "0.5-0.9": 0, "<0.5": 0}
    classes = set()

    for ax in doc.axioms():
        if ax.node.functor != "SubClassOf":
            continue
        n_subclass += 1
        args = ax.node.args
        if len(args) < 2:
            continue
        sub_arg, sup_arg = args[-2], args[-1]

        prob = None
        for a in args[:-2]:                  # leading Annotation(...) args
            if (isinstance(a, ofn.Node) and a.functor == "Annotation"
                    and len(a.args) >= 2
                    and isinstance(a.args[0], ofn.Iri)
                    and BORN_PROBABILITY_IRI in a.args[0].value
                    and isinstance(a.args[1], ofn.Literal)):
                try:
                    prob = float(a.args[1].lexical)
                except ValueError:
                    prob = None
                break

        if prob is None:
            n_det += 1
            continue
        n_prob += 1
        if   prob >= 1.0: bins["=1.0"]    += 1
        elif prob >= 0.9: bins["0.9-1.0"] += 1
        elif prob >= 0.5: bins["0.5-0.9"] += 1
        else:             bins["<0.5"]    += 1

        if isinstance(sub_arg, ofn.Iri) and isinstance(sup_arg, ofn.Iri):
            edges[sub_arg.value].append((sup_arg.value, prob))
            classes.add(sub_arg.value); classes.add(sup_arg.value)

    # Compute the most-probable path's compound probability from each class
    # to every reachable ancestor (depth-limited iterative DFS, max-product).
    marginals = defaultdict(dict)
    for start in classes:
        stack = [(start, 1.0, frozenset({start}))]
        while stack:
            node, cum, visited = stack.pop()
            for parent, p in edges.get(node, ()):
                if parent in visited:
                    continue
                pp = cum * p
                if marginals[start].get(parent, 0.0) < pp:
                    marginals[start][parent] = pp
                    stack.append((parent, pp, visited | {parent}))

    inferred = []
    for sub, sups in marginals.items():
        for sup, p in sups.items():
            inferred.append((sub, sup, round(p, 4)))
    inferred.sort(key=lambda t: (t[2], t[0], t[1]))

    return {
        "reasoner": "BORN",
        "mode": "built-in",
        "file": born_owl_path,
        "counts": {
            "subclass_axioms": n_subclass,
            "with_probability": n_prob,
            "without_probability": n_det,
            "classes_involved": len(classes),
        },
        "distribution": bins,
        "marginal_total": len(inferred),
        "most_uncertain": inferred[:40],
        "most_certain":   list(reversed(inferred[-40:])),
    }


def _run_born_external(born_owl_path, jar):
    """Optional path: shell out to a real BORN executable jar."""
    if not _java_works():
        raise ReasonerUnavailable(
            "BORN external mode needs a Java runtime.\n"
            "Install one, e.g.:  brew install --cask temurin")
    java = shutil.which("java")
    try:
        proc = subprocess.run([java, "-jar", jar, born_owl_path],
                              capture_output=True, timeout=240, text=True)
    except subprocess.TimeoutExpired:
        raise ReasonerUnavailable("BORN took longer than 240s and was stopped.")
    except Exception as exc:
        raise ReasonerUnavailable("BORN execution failed: %s" % exc)
    return {
        "reasoner": "BORN",
        "mode": "external",
        "born_jar": jar,
        "file": born_owl_path,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def run_reasoner_born(born_owl_path):
    """Run BORN. Uses the built-in analyser by default; if BORN_JAR is set to
    a runnable jar, that's used instead."""
    import os
    jar = os.environ.get("BORN_JAR")
    if jar and os.path.isfile(jar):
        return _run_born_external(born_owl_path, jar)
    return run_born_builtin(born_owl_path)


def reasoners_available():
    """Availability and requirements for every supported reasoner."""
    try:
        import owlready2  # noqa: F401
        ow_present, ow_msg = True, ""
    except Exception:
        ow_present = False
        ow_msg = "owlready2 not installed - `python3 -m pip install owlready2`"
    java_present = _java_works()
    java_msg = "" if java_present else "no working Java runtime found"
    out = []
    for name in ("HermiT", "Pellet"):
        problems = []
        if not ow_present: problems.append(ow_msg)
        if not java_present: problems.append(java_msg)
        out.append({
            "name": name, "key": name.lower(),
            "available": ow_present and java_present,
            "requires": "owlready2 + Java",
            "message": "Ready." if (ow_present and java_present)
                       else " · ".join(problems),
        })
    # BORN: built-in analyser is always available (pure Python).
    import os
    jar = os.environ.get("BORN_JAR")
    has_jar = bool(jar and os.path.isfile(jar))
    out.append({
        "name": "BORN", "key": "born",
        "available": True,
        "requires": ("External born.jar (BORN_JAR)" if has_jar
                     else "Built-in (pure Python, no external dependency)"),
        "born_jar": jar if has_jar else None,
        "message": ("Built-in analyser ready. External BORN_JAR detected: "
                    + jar) if has_jar
                   else "Built-in analyser ready. (Set BORN_JAR to use an "
                        "external born.jar instead.)",
    })
    return out


# ---------------------------------------------------------------------------

def export_rdfxml(model, path):
    """Translate the model and save it as an RDF/XML .owl file.

    Needs owlready2 but not Java. Returns the count of skipped axioms."""
    _import_owlready()
    world, onto, ents, skipped = _Translator(model).build()
    onto.save(file=path, format="rdfxml")
    return skipped


def build_world(model):
    """Translate the model into an owlready2 world (no reasoning).

    Needs owlready2 but not Java. Returns (world, onto, skipped) - used by
    the SPARQL exam tool."""
    _import_owlready()
    world, onto, ents, skipped = _Translator(model).build()
    return world, onto, skipped
