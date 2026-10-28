"""CLI composition root tests: explicit single-scope local ingestion and recall."""

import json

from weft import cli


class FakeEmbedder:
    model_id = "test/model@v1"

    def embed_document(self, text):
        return [1.0] + [0.0] * 1023

    def embed_query(self, text):
        return [1.0] + [0.0] * 1023


def test_ingest_and_recall_are_local_single_scope_and_vectorized(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "default_text_embedder", lambda: FakeEmbedder())
    db = tmp_path / "weft.db"
    note = tmp_path / "note.md"
    note.write_text("The lighthouse keepers record the tides each night.", encoding="utf-8")

    assert cli.main(["--db", str(db), "ingest", "--scope", "coast", "--file", str(note)]) == 0
    ingested = json.loads(capsys.readouterr().out)
    assert ingested["status"] == "ingested"
    assert ingested["scope"] == "coast"
    assert ingested["vectors_written"] is True

    assert cli.main(["--db", str(db), "recall", "--scope", "coast", "--query", "who records tides?"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "hybrid"
    assert result["items"]
    assert result["items"][0]["scope"] == "coast"


def test_cli_rejects_missing_model_without_silent_fallback(tmp_path, monkeypatch, capsys):
    def missing():
        raise RuntimeError("embedding model not present")

    monkeypatch.setattr(cli, "default_text_embedder", missing)
    note = tmp_path / "note.txt"
    note.write_text("Local memory text", encoding="utf-8")
    code = cli.main(["--db", str(tmp_path / "weft.db"), "ingest", "--scope", "local", "--file", str(note)])
    assert code != 0
    assert "embedding model not present" in capsys.readouterr().err


def test_cli_scope_isolation(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "default_text_embedder", lambda: FakeEmbedder())
    db = tmp_path / "weft.db"
    note = tmp_path / "secret.txt"
    note.write_text("Only the coastal archivist records the rare blue tide.", encoding="utf-8")
    cli.main(["--db", str(db), "ingest", "--scope", "coast", "--file", str(note)])
    capsys.readouterr()

    code = cli.main(["--db", str(db), "recall", "--scope", "inland", "--query", "blue tide"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["items"] == []
    assert result["scope_consulted"] == ["inland"]
