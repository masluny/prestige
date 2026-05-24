"""
core/ - the headless ontology engine.

Pure Python, no UI library imports. Anything that wants to drive the editor
(a web server, a CLI, a notebook, another GUI) talks to this package.

Modules:
  ofn        - OWL 2 Functional-Syntax tokenizer, parser and serializer.
  model      - Ontology model with all edit operations, validation, undo/redo.
  reasoner   - Optional owlready2 / HermiT bridge + RDF/XML export.
  examtools  - Analysis functions (queries, pitfall scan, metrics, glossary, diff).
  graphview  - Graphviz DOT generation + SVG/PNG rendering.
"""

from . import ofn, model, reasoner, examtools, graphview
from .ofn import (
    Iri, Node, Literal, Document, Axiom, Trivia,
    parse, load, local_name, prefix_of, AXIOM_KEYWORDS,
)
from .model import (
    Ontology, ModelError,
    OBJECT_CHARACTERISTICS, CHARACTERISTIC_LABELS,
)

__all__ = [
    "ofn", "model", "reasoner", "examtools", "graphview",
    "Iri", "Node", "Literal", "Document", "Axiom", "Trivia",
    "parse", "load", "local_name", "prefix_of", "AXIOM_KEYWORDS",
    "Ontology", "ModelError",
    "OBJECT_CHARACTERISTICS", "CHARACTERISTIC_LABELS",
]
