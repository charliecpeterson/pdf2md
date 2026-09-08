

def test_auto_engine_picks_docling_for_a_born_digital_document(monkeypatch):
    from pathlib import Path

    from pdf2md.config import Config
    from pdf2md.engines import select

    monkeypatch.setattr(select, "scanned_share", lambda _p: 0.0)

    assert select.auto_engine(Config(engine="auto"), Path("x.pdf")) == "docling"


def test_auto_engine_picks_mineru_for_a_scan_when_it_is_installed(monkeypatch):
    from pathlib import Path

    import pdf2md.engines.mineru as mineru
    from pdf2md.config import Config
    from pdf2md.engines import select

    monkeypatch.setattr(select, "scanned_share", lambda _p: 1.0)
    monkeypatch.setattr(mineru, "MinerUEngine", lambda *a, **k: object())

    assert select.auto_engine(Config(engine="auto"), Path("x.pdf")) == "mineru"


def test_auto_engine_falls_back_when_mineru_is_configured_but_absent(monkeypatch):
    """An optional engine that is not installed is not a failed run."""
    from pathlib import Path

    import pdf2md.engines.mineru as mineru
    from pdf2md.config import Config
    from pdf2md.engines import select

    monkeypatch.setattr(select, "scanned_share", lambda _p: 1.0)

    def missing(*_a, **_k):
        raise RuntimeError("MinerU executable not found: mineru")

    monkeypatch.setattr(mineru, "MinerUEngine", missing)

    assert select.auto_engine(Config(engine="auto"), Path("x.pdf")) == "docling"
