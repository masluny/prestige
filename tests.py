"""
tests.py - self-checks for the functional-syntax engine and ontology model.

Run with:  python3 tests.py
No third-party dependencies; uses the bundled ontology.owl as fixture.
"""

import os
import sys

from core import ofn, model

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "ontology.owl")

_passed = 0
_failed = 0


def check(label, condition):
    global _passed, _failed
    if condition:
        _passed += 1
        print("  ok   - %s" % label)
    else:
        _failed += 1
        print("  FAIL - %s" % label)


def axiom_multiset(doc):
    """Normalised multiset of axioms (rendered from AST) for equality tests."""
    from collections import Counter
    return Counter(ax.node.render() for ax in doc.axioms())


# ---------------------------------------------------------------------------

def test_roundtrip():
    print("[1] lossless round-trip")
    original = open(FIXTURE, encoding="utf-8").read()
    doc = ofn.parse(original)
    check("serialize() is byte-identical to the source",
          doc.serialize() == original)
    check("all 2144 axioms parsed", len(doc.axioms()) == 2144)


def test_minimal_diff():
    print("[2] minimal-diff editing")
    ont = model.Ontology.load(FIXTURE)
    before = {ax.node.render() for ax in ont.doc.axioms()}
    ont.create_class("UnitTestSign", parents=["ProhibitorySign"],
                     label="Unit Test Sign")
    after = {ax.node.render() for ax in ont.doc.axioms()}
    new = after - before
    check("creating a class adds a Declaration",
          any(s.startswith("Declaration(Class(roadsigns:UnitTestSign")
              for s in new))
    check("creating a class adds a SubClassOf to the chosen parent",
          "SubClassOf(roadsigns:UnitTestSign roadsigns:ProhibitorySign)" in new)
    check("creating a class adds the rdfs:label annotation",
          any(s.startswith("AnnotationAssertion(rdfs:label "
                            "roadsigns:UnitTestSign") for s in new))
    # untouched axioms keep their exact source text
    untouched_clean = all(
        (not ax.dirty) for ax in ont.doc.axioms() if ax.raw is not None)
    check("pre-existing axioms are not marked dirty", untouched_clean)
    # re-parse the edited document
    reparsed = ofn.parse(ont.serialize())
    check("edited document re-parses cleanly",
          len(reparsed.axioms()) == len(ont.doc.axioms()))


def test_move():
    print("[3] move / re-parent")
    ont = model.Ontology.load(FIXTURE)
    ont.create_class("MoveMeSign", parents=["ProhibitorySign"])
    ont.move_class("MoveMeSign", ["MandatorySign"])
    check("class now has the new parent",
          ont.parents["roadsigns:MoveMeSign"] == ["roadsigns:MandatorySign"])
    check("old parent edge is gone",
          "roadsigns:ProhibitorySign" not in ont.parents["roadsigns:MoveMeSign"])


def test_cycle_guard():
    print("[4] cycle detection")
    ont = model.Ontology.load(FIXTURE)
    ont.create_class("ParentSign", parents=["RoadSign"])
    ont.create_class("ChildSign", parents=["ParentSign"])
    raised = False
    try:
        ont.move_class("ParentSign", ["ChildSign"])
    except model.ModelError:
        raised = True
    check("moving a class under its own descendant is rejected", raised)


def test_rename():
    print("[5] rename propagation")
    ont = model.Ontology.load(FIXTURE)
    refs_before = len(ont.refs.get("roadsigns:ProhibitorySign", []))
    ont.rename_entity("ProhibitorySign", "ProhibitorySignRENAMED")
    check("old name no longer declared",
          "roadsigns:ProhibitorySign" not in ont.declared)
    check("new name is declared",
          "roadsigns:ProhibitorySignRENAMED" in ont.declared)
    check("every reference moved to the new name",
          len(ont.refs.get("roadsigns:ProhibitorySignRENAMED", [])) == refs_before)
    check("no axiom still mentions the old name",
          "roadsigns:ProhibitorySign" not in ont.refs)


def test_delete_reparent():
    print("[6] delete with re-parent")
    ont = model.Ontology.load(FIXTURE)
    ont.create_class("MidSign", parents=["RoadSign"])
    ont.create_class("LeafSign", parents=["MidSign"])
    ont.delete_class("MidSign", mode="reparent")
    check("deleted class is gone", "roadsigns:MidSign" not in ont.declared)
    check("child was re-parented to the grandparent",
          "roadsigns:RoadSign" in ont.parents.get("roadsigns:LeafSign", []))


def test_delete_subtree():
    print("[7] delete whole subtree")
    ont = model.Ontology.load(FIXTURE)
    ont.create_class("DoomedSign", parents=["RoadSign"])
    ont.create_class("DoomedChildSign", parents=["DoomedSign"])
    ont.delete_class("DoomedSign", mode="subtree")
    check("parent removed", "roadsigns:DoomedSign" not in ont.declared)
    check("descendant removed too",
          "roadsigns:DoomedChildSign" not in ont.declared)


def test_undo_redo():
    print("[8] undo / redo")
    ont = model.Ontology.load(FIXTURE)
    n0 = len(ont.classes())
    ont.create_class("TempSign", parents=["RoadSign"])
    check("class count rose after create", len(ont.classes()) == n0 + 1)
    ont.undo()
    check("undo restored the class count", len(ont.classes()) == n0)
    ont.redo()
    check("redo re-applied the create", len(ont.classes()) == n0 + 1)


def test_restriction_and_raw():
    print("[9] restrictions and raw axioms")
    ont = model.Ontology.load(FIXTURE)
    ont.create_class("RestrictedSign", parents=["RoadSign"])
    ont.add_restriction("RestrictedSign", "hasShape", "Triangle", kind="value")
    defs = ont.class_definitions("RestrictedSign")
    check("restriction axiom is recorded on the class",
          any("ObjectHasValue" in d for d in defs))
    ont.add_raw_axiom(
        "SubClassOf(roadsigns:RestrictedSign roadsigns:RoadSign)")
    check("raw axiom accepted", True)
    rejected = False
    try:
        ont.add_raw_axiom("Nonsense(roadsigns:Foo)")
    except model.ModelError:
        rejected = True
    check("non-axiom keyword in raw input is rejected", rejected)


def test_properties_individuals():
    print("[10] properties and individuals")
    ont = model.Ontology.load(FIXTURE)
    ont.create_object_property("testRelation", domain="RoadSign",
                               range_="RoadSign",
                               characteristics=["FunctionalObjectProperty"])
    check("object property declared",
          "roadsigns:testRelation" in ont.entities["ObjectProperty"])
    ont.create_data_property("testValue", domain="RoadSign",
                             range_="xsd:integer", functional=True)
    check("data property declared",
          "roadsigns:testValue" in ont.entities["DataProperty"])
    ont.create_individual("TestSignInstance", types=["RoadSign"])
    check("individual declared",
          "roadsigns:TestSignInstance" in ont.entities["NamedIndividual"])


def test_validation():
    print("[11] validation")
    ont = model.Ontology.load(FIXTURE)
    issues = ont.validate()
    check("validation returns a list", isinstance(issues, list))
    # the source ontology genuinely contains a self-referential axiom:
    #   SubClassOf(roadsigns:LEDSpeedLimitSign roadsigns:LEDSpeedLimitSign)
    cycles = [i for i in issues
              if i["severity"] == "error" and "Cycle" in i["message"]]
    check("validator flags the real LEDSpeedLimitSign self-loop",
          any("LEDSpeedLimitSign" in i["message"] for i in cycles))
    # after fixing it, no cycles should remain
    ont.remove_parent("LEDSpeedLimitSign", "LEDSpeedLimitSign")
    cycles2 = [i for i in ont.validate()
               if i["severity"] == "error" and "Cycle" in i["message"]]
    check("removing the self-loop clears the cycle", not cycles2)
    # introduce a deliberate undeclared reference
    ont.add_raw_axiom("SubClassOf(roadsigns:RoadSign roadsigns:GhostClass)")
    issues2 = ont.validate()
    check("an undeclared reference is detected",
          any("GhostClass" in (i["message"] + i["entity"]) for i in issues2))


def test_save(tmp="_test_output.owl"):
    print("[12] save + reopen")
    ont = model.Ontology.load(FIXTURE)
    ont.create_class("SavedSign", parents=["RoadSign"], comment="saved")
    out = os.path.join(HERE, tmp)
    ont.save(out, make_backup=False)
    reopened = model.Ontology.load(out)
    check("saved class survives a reload",
          "roadsigns:SavedSign" in reopened.declared)
    os.remove(out)


def test_examtools():
    print("[13] exam tools")
    from core import examtools
    ont = model.Ontology.load(FIXTURE)
    subs = examtools.run_query(ont, "all_subclasses", cls="RoadSign")
    check("query: RoadSign has many subclasses", len(subs) > 100)
    tri = examtools.run_query(ont, "restriction_value",
                              prop="hasShape", value="Triangle")
    check("query: restriction hasShape=Triangle finds class(es)",
          len(tri) >= 1)
    issues = examtools.pitfall_scan(ont)
    check("pitfall scan returns issues", len(issues) > 0)
    check("pitfall scan flags missing labels",
          any(i["category"] == "Missing rdfs:label" for i in issues))
    stats = dict(examtools.metrics(ont))
    check("metrics reports 761 classes", stats.get("Classes") == 761)
    outline = examtools.hierarchy_outline(ont, root="ProhibitorySign")
    check("hierarchy outline is indented", "    " in outline)
    for fmt in ("text", "markdown", "csv"):
        check("glossary renders as %s" % fmt,
              len(examtools.glossary(ont, fmt)) > 100)
    diff = examtools.diff_files(ont, FIXTURE)
    check("diff of a file against itself is empty",
          not diff["added_axioms"] and not diff["removed_axioms"])


def test_graphview():
    print("[14] graph view (Graphviz DOT)")
    from core import graphview
    ont = model.Ontology.load(FIXTURE)
    dot, n = graphview.build_dot(ont, focus="roadsigns:RoadSign",
                                 up=0, down=1, whole=False)
    check("scoped DOT mentions the focus class", "RoadSign" in dot)
    check("scoped DOT has at least the focus + children", n >= 2)
    check("DOT is well-formed",
          dot.startswith("digraph") and dot.rstrip().endswith("}"))
    _dot_all, n_all = graphview.build_dot(ont, whole=True)
    check("whole-ontology DOT covers every class", n_all == 761)


def main():
    for fn in (test_roundtrip, test_minimal_diff, test_move, test_cycle_guard,
               test_rename, test_delete_reparent, test_delete_subtree,
               test_undo_redo, test_restriction_and_raw,
               test_properties_individuals, test_validation, test_save,
               test_examtools, test_graphview):
        fn()
    print("\n%d passed, %d failed" % (_passed, _failed))
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
