"""Method summaries: what is read, what is kept, and the file."""

import json

import pytest

from cra.core.library import manifest
from cra.core.library import summaries as store
from cra.core.library.records import FullText, Paper

BODY = "Body text of a paper. " * 200  # well over the shortest usable full text


def paper(doi="10.1/a", title="A title", abstract="An abstract."):
    return Paper(doi=doi, title=title, authors=(), year="2024", abstract=abstract)


def full(doi, text):
    return FullText(
        doi=doi, text=text, source="pdf", source_origin="", url="", fetched_at=""
    )


def answer(**overrides):
    return {"summary": "Films were grown.", "approach": "experimental", **overrides}


# ---- the text a model is shown ------------------------------------------------


def test_a_full_text_that_fits_is_read_whole():
    assert store.select_text("An abstract.", BODY) == ("fulltext", BODY.strip())


def test_a_stub_of_a_full_text_gives_way_to_the_abstract():
    # a scan or a landing page: a few lines are not the paper
    assert store.select_text("An abstract.", "Cookies on this site.") == (
        "abstract",
        "An abstract.",
    )


def test_a_paper_with_nothing_to_read_has_no_source():
    assert store.select_text("  ", "Cookies on this site.") == ("", "")
    assert store.select_text("", "") == ("", "")


def test_a_long_text_keeps_its_opening_and_its_methods_section():
    text = (
        "Introduction. " * 7_000  # 98,000 characters of background
        + "\n## 2. Experimental\nWe deposited the films at 300 C.\n"
        + "Discussion. " * 7_000
    )
    clipped = store.clip(text)
    assert len(clipped) <= store.MAX_TEXT_CHARS + 20
    assert clipped.startswith("Introduction.")
    assert "We deposited the films at 300 C." in clipped


def test_a_long_text_without_a_methods_heading_is_cut_at_the_limit():
    clipped = store.clip("Words. " * 40_000)
    assert len(clipped) == store.MAX_TEXT_CHARS


def test_what_a_record_was_made_from_changes_its_hash():
    base = store.input_hash("T", "fulltext", "text")
    assert base == store.input_hash("T", "fulltext", "text")
    assert base != store.input_hash("T", "fulltext", "other text")
    assert base != store.input_hash("T", "abstract", "text")
    assert base != store.input_hash("Another title", "fulltext", "text")
    assert store.input_hash("T", "", "") == ""


# ---- what is kept of an answer -------------------------------------------------


def test_a_good_answer_is_kept_with_every_field_present():
    fields = store.validate(
        answer(
            material_system="Pt thin films",
            characterization_methods=["XRD", "resistivity"],
            results="Strong (111) orientation.",
        )
    )
    assert fields == {
        "summary": "Films were grown.",
        "approach": "experimental",
        "material_system": "Pt thin films",
        "processing_method": "",
        "results": "Strong (111) orientation.",
        "novelty": "",
        "limitations": "",
        "characterization_methods": ["XRD", "resistivity"],
        "properties_investigated": [],
    }


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("Experimental", "experimental"),
        (" simulation ", "computational"),
        ("theory", "theoretical"),
        ("both", "mixed"),
        ("Review", "review"),
    ],
)
def test_the_kind_of_work_is_one_of_a_closed_set(given, kept):
    assert store.validate(answer(approach=given))["approach"] == kept


@pytest.mark.parametrize(
    "bad",
    [
        answer(approach="alchemy"),
        answer(approach=""),
        answer(summary=""),
        {"approach": "mixed"},
        {},
    ],
)
def test_an_answer_without_a_summary_or_a_known_kind_is_refused(bad):
    assert store.validate(bad) is None


def test_every_field_is_capped_and_cut_at_a_word():
    fields = store.validate(
        answer(summary="word " * 100, results="x" * 1000, material_system="y " * 100)
    )
    assert len(fields["summary"]) <= store.LIMITS["summary"]
    assert fields["summary"].endswith("…")
    assert not fields["summary"].endswith(" …")
    assert len(fields["results"]) <= store.LIMITS["results"]
    assert len(fields["material_system"]) <= store.LIMITS["material_system"]


def test_lists_are_deduplicated_capped_and_split_from_a_string():
    fields = store.validate(
        answer(
            characterization_methods=["XRD", "xrd", "SEM", "", None, *"abcdefgh"],
            properties_investigated="film uniformity; resistivity, orientation",
        )
    )
    assert fields["characterization_methods"][:2] == ["XRD", "SEM"]
    assert len(fields["characterization_methods"]) == store.LIST_ITEMS
    assert fields["properties_investigated"] == [
        "film uniformity",
        "resistivity",
        "orientation",
    ]


def test_the_object_is_found_around_prose_and_reasoning():
    raw = (
        "<think>The answer looks like {this} maybe.</think>\n"
        'Here you go: {"summary": "S.", "approach": "review"} Hope it helps.'
    )
    assert store.parse(raw) == {"summary": "S.", "approach": "review"}


@pytest.mark.parametrize("raw", ["", "no json here", "[1, 2]", "{broken", None])
def test_an_answer_that_is_no_object_parses_to_nothing(raw):
    assert store.parse(raw) is None


# ---- what a run has to do ------------------------------------------------------


def test_a_new_paper_is_asked_for_and_a_current_one_is_not():
    papers = {"10.1/a": paper()}
    first = store.plan(papers, {}, {})
    assert [j.doi for j in first.jobs] == ["10.1/a"]
    assert first.jobs[0].source == "abstract"
    records = {"10.1/a": store.record({"summary": "S."}, first.jobs[0], "m")}
    again = store.plan(papers, {}, records)
    assert (again.jobs, again.unchanged) == ([], 1)


def test_a_paper_whose_text_changed_is_asked_for_again():
    records = {
        "10.1/a": store.record(
            {"summary": "S."}, store.plan({"10.1/a": paper()}, {}, {}).jobs[0], "m"
        )
    }
    changed = {"10.1/a": paper(abstract="A different abstract.")}
    assert [j.doi for j in store.plan(changed, {}, records).jobs] == ["10.1/a"]
    # and so is one whose full text turned up since
    gained = store.plan({"10.1/a": paper()}, {"10.1/a": full("10.1/a", BODY)}, records)
    assert gained.jobs[0].source == "fulltext"


def test_force_and_only_choose_the_papers():
    papers = {"10.1/a": paper("10.1/a"), "10.1/b": paper("10.1/b", abstract="Other.")}
    done = {j.doi: store.record({}, j, "m") for j in store.plan(papers, {}, {}).jobs}
    assert store.plan(papers, {}, done).jobs == []
    assert len(store.plan(papers, {}, done, force=True).jobs) == 2
    only = store.plan(papers, {}, {}, only={"10.1/b"})
    assert [j.doi for j in only.jobs] == ["10.1/b"]


def test_a_paper_with_nothing_to_read_is_recorded_once_and_not_asked_for():
    papers = {"10.1/a": paper(abstract="")}
    first = store.plan(papers, {}, {})
    assert (first.jobs, first.no_text) == ([], ["10.1/a"])
    second = store.plan(papers, {}, {"10.1/a": store.no_text_record()})
    assert (second.no_text, second.unchanged) == ([], 1)


# ---- the file --------------------------------------------------------------------


def test_the_file_round_trips_and_keeps_only_finished_records(tmp_path):
    path = tmp_path / "summaries.json"
    job = store.plan({"10.1/a": paper()}, {}, {}).jobs[0]
    fields = store.validate(answer(characterization_methods=["XRD"]))
    header = store.write(
        path,
        {
            "10.1/a": store.record(fields, job, "model-1"),
            "10.1/b": store.no_text_record(),
        },
        "model-1",
    )
    assert header["prompt_version"] == store.PROMPT_VERSION
    assert not list(tmp_path.glob("*.part")), "the swap leaves no half-written file"
    loaded = store.load(json.loads(path.read_text(encoding="utf-8")))
    assert list(loaded.records) == ["10.1/a"]
    one = loaded.records["10.1/a"]
    assert (one.approach, one.source, one.model) == (
        "experimental",
        "abstract",
        "model-1",
    )
    assert one.characterization_methods == ("XRD",)
    assert loaded.model == "model-1"
    assert len(loaded) == 1


def test_a_record_without_its_own_model_is_credited_to_the_files():
    loaded = store.load(
        {
            "model": "header-model",
            "records": {
                "10.1/a": {"status": "ok", "summary": "S.", "approach": "mixed"}
            },
        }
    )
    assert loaded.records["10.1/a"].model == "header-model"


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ([], TypeError),
        ({"records": []}, TypeError),
        ({"records": {"10.1/a": "text"}}, TypeError),
        ({"records": {"10.1/a": {"status": "ok", "approach": "mixed"}}}, ValueError),
    ],
)
def test_a_malformed_file_is_refused(raw, error):
    with pytest.raises(error):
        store.load(raw)


def test_a_missing_or_unreadable_file_reads_as_empty(tmp_path):
    assert store.read(tmp_path / "absent.json") == {"records": {}}
    (tmp_path / "bad.json").write_text("{not json")
    assert store.read(tmp_path / "bad.json") == {"records": {}}


def test_a_rewritten_manifest_keeps_who_made_what(tmp_path):
    (tmp_path / "summaries.json").write_text("{}")
    manifest.write(tmp_path, {}, provenance={"summaries": {"model": "m"}})
    manifest.write(tmp_path, {"papers": 1})
    assert manifest.read(tmp_path)["provenance"] == {"summaries": {"model": "m"}}
    manifest.write(tmp_path, {}, provenance={})
    assert manifest.read(tmp_path)["provenance"] == {}
