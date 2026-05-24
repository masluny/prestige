# Prestige  ·  an OWL ontology editor

<img src="webapp/static/logo.png" align="right" width="120">

A point-and-click editor for OWL 2 Functional-Syntax ontologies. Prestige runs
as a small local web server that serves a single-page web UI in your browser
with a **modern dark theme, rounded "island" layout** and proper
keyboard / accessibility support.

- **Lossless** — axioms you don't touch are saved back byte-for-byte.
- **Safe** — every edit is undoable; a timestamped `.bak` backup is written
  before each save.
- **Complete** — every editing task plus all the analysis & exam tools are
  reachable from one window. Three reasoners are built in: **HermiT**,
  **Pellet** and **BORN** (the last works without any external dependency).

---

## 1. Requirements

| For                          | What                                       |
|------------------------------|--------------------------------------------|
| The editor (always needed)   | Python 3 + `pip install fastapi 'uvicorn[standard]' python-multipart` |
| Reasoner, RDF export, SPARQL | `pip install owlready2`                    |
| Reasoner only                | a Java runtime (`brew install --cask temurin`) |
| Graph view                   | Graphviz (`brew install graphviz`)         |

## 2. Run it

```bash
cd ~/*path*/roadsign_ontology_editor
python3 app.py
```

A local server starts on `http://127.0.0.1:8765/` and the editor opens in
your default browser. **Ctrl+C** in the terminal stops it. (Set
`PORT=9000` or `NO_BROWSER=1` if you want to override either.)

On first load the editor asks you to **upload an OWL file** — drop it on the
splash screen or click to pick. The file is stored next to the project and
becomes the working file (Save writes back to it with a timestamped `.bak`).
To load a different file later, use **Tools → Open ontology…**.

## 3. The window — islands at a glance

- **Top island** — the brand on the left, the toolbar on the right:
  *Ontology ▾* (every editing command), *Tools ▾* (Validate, Statistics,
  Reasoner), *Extra tools ▾* (the seven analysis tools), *Graph*, *Undo*,
  *Redo*, *Save*.
- **Left island** — search box (Ctrl+F) + tabs for *Classes / Object props /
  Data props / Individuals*. The Classes tab is a real tree with
  expand/collapse and arrow-key navigation. Search filters live as you type
  and auto-expands ancestors of matches. Right-click any class for a
  context menu.
- **Right island** — Protégé-style details of the selected entity: name in
  the accent yellow, the kind pill, action buttons, the *Annotations* card
  (editable label & comment), then *Equivalent to*, *SubClass of* (with
  ± parent buttons), *Disjoint with*, *Subclasses*, *Instances* (for a
  class — analogous sections for properties and individuals). Every row
  with a navigable target is a single click away. "Appears in axioms" is at
  the bottom, collapsed by default.
- **Status island** — "Ready" / activity + live entity counts.

## 4. How to do the common tasks

| Teacher asks…                     | Do this |
|-----------------------------------|---------|
| **Create a class**                | *Ontology → New class…* (or **Ctrl+N**, or the **+ New class** button bottom-left) |
| **Subclass of X**                 | Select X, **+ Subclass** in the right panel header |
| **Move (re-parent)**              | Select a class, **Move** button → pick new parent(s) (cycles blocked) |
| **Rename anywhere**               | Select the entity, **Rename** — the IRI is rewritten in every axiom |
| **Delete**                        | **Delete** → choose re-parent children / delete subtree / block |
| **Edit label / comment**          | Edit the fields in the *Annotations* card, click **Apply annotations** |
| **Add a restriction**             | Select a class, **+ Restriction** (e.g. `hasShape value Triangle`) |
| **Disjoint / equivalent classes** | *Ontology → Add disjoint…* / *Add equivalent…* |
| **Object / data property**        | *Ontology → New object/data property…* |
| **Individual**                    | *Ontology → New individual…* |
| **Anything else**                 | *Ontology → Add raw axiom…* — type any functional-syntax axiom, validated before being added |

Shortcuts: **Ctrl+Z / Ctrl+Y** undo/redo · **Ctrl+S** save · **Ctrl+N** new
class · **Ctrl+F** focus search · **Esc** close modal.

## 5. Tools

- **Open ontology…** — switch to another `.owl` file at any time.
- **Validate** — cycles, undeclared entities, duplicate declarations.
- **Statistics** — counts, depth, etc.
- **Run reasoner** — choose **HermiT**, **Pellet** or **BORN** from the
  dropdown. HermiT/Pellet check consistency, list unsatisfiable classes,
  and report inferred subclass relations (need `owlready2` + Java; the
  modal shows what's missing). **BORN** runs a Bayesian reasoner against a
  BORN-style copy of the ontology (see next).
- **Create BORN ontology file…** — writes `<name>_BORN.owl` next to the
  loaded ontology, with a probability annotation on every `SubClassOf`
  axiom (default `1.0`, editable). You can then download the file or run
  the BORN reasoner against it. To actually run BORN, get `born.jar` from
  <https://github.com/julianmendez/born> and either drop it into the
  project folder or set the `BORN_JAR` environment variable.

## 6. Extra tools

| Tool | What it does |
|------|--------------|
| **Competency-question query** | Direct/all sub- and superclasses, siblings, instances, **classes with restriction `property = value`** (e.g. "all signs with `hasShape value Triangle`"), classes using a property, roots, leaves, defined classes, missing label/comment. Double-click a result to jump to it. |
| **Pitfall & quality scan** | OOPS!-style: cycles, undeclared entities, missing `rdfs:label`/`rdfs:comment`, properties with no domain/range, duplicate labels, sibling classes not declared disjoint — grouped by category. |
| **Hierarchy outline** | Indented taxonomy text export. |
| **Metrics report** | Counts, depth, annotation coverage %, defined vs primitive classes — for the project defence. |
| **Glossary / data dictionary** | Every class with its label and comment, downloadable as text, Markdown or CSV. |
| **Compare with another file** | Axiom-level diff against any other `.owl` you upload. |
| **SPARQL query** | Real SPARQL via owlready2 (needs `pip install owlready2`). |

## 7. Graph view (Graphviz)

**Graph** in the toolbar opens an interactive SVG graph. Pick a focus class +
how many ancestor/descendant levels, optionally show property restrictions as
dashed edges, choose one of **six layout algorithms** (`dot · sfdp · neato ·
fdp · circo · twopi`). Drag to pan, wheel to zoom. **Download SVG** saves the
result. Needs Graphviz (`brew install graphviz`).

## 8. Saving

**Save** writes the file back in OWL Functional Syntax. Untouched axioms are
written back byte-for-byte; only new/changed axioms are re-rendered, so the
diff stays small and the file still opens in Protégé. A timestamped
`ontology.owl.YYYYMMDD-HHMMSS.bak` is created before every overwrite.

## 9. Project files

```
roadsign_ontology_editor/
  app.py              Launcher: starts FastAPI + opens the browser
  core/               Headless engine (no UI imports anywhere)
    ofn.py            OWL Functional Syntax tokenizer / parser / serializer
    model.py          Ontology model + every edit operation + undo
    reasoner.py       Optional owlready2 / HermiT bridge + RDF/XML export
    examtools.py      run_query, pitfall_scan, metrics, glossary, diff
    graphview.py      DOT generation + SVG/PNG rendering
  webapp/
    server.py         FastAPI REST endpoints
    static/
      index.html      Single-page UI shell
      styles.css      Ayu Mirage palette + island layout
      app.js          Vanilla JS app (no build step)
  tests.py            46 self-checks for the core
  ontology.owl        Working copy of the road-signs ontology
```

## 10. Tests

```bash
python3 tests.py
```

Round-trip, every edit operation, cycle detection, rename propagation,
undo/redo, validation, save/reload, every analysis function, Graphviz DOT
generation — 46 checks against the bundled ontology.
