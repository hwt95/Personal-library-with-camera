import sqlite3
import pytest

from app.database import LibraryDB
from app.domain import normalize_isbn, ScanGate


@pytest.fixture
def db(tmp_path):
    result = LibraryDB(tmp_path / "books.sqlite3", create=True)
    yield result
    result.close()


def book(title="测试图书", **extra):
    return {"title": title, "clc_code": "I247.57", **extra}


def test_copies_do_not_reuse_or_renumber(db):
    first, code = db.save(book(), 2000)
    assert code == "I247.57-000001"
    ids = [db.save(book(), 2001+i, book_id=1)[0] for i in range(12)]
    assert db.search()[-1]["code"].endswith("(12)")
    assert db.search()[2]["code"].endswith("(2)")
    db.delete_copy(ids[-1])
    _, code = db.save(book(), 2026, book_id=1)
    assert code.endswith("(13)")
    for item in db.search():
        db.delete_copy(item["id"])
    _, code = db.save(book(), 2026, book_id=1)
    assert code.endswith("(14)")
    _, code = db.save(book("另一本"), 2020)
    assert code == "I247.57-000002"


def test_reclassify_updates_all_codes_preserves_ids(db):
    first, _ = db.save(book(clc_code=""), 2000)
    second, _ = db.save(book(clc_code=""), 2001, book_id=1)
    assert db.search()[0]["code"] == "PENDING-000001"
    db.save(book(clc_code="TP303"), 2000, book_id=1, copy_id=first)
    assert [(r["id"], r["code"]) for r in db.search()] == [(first, "TP303-000001"), (second, "TP303-000001(1)")]


def test_save_rolls_back_both_book_and_copy(db):
    db.conn.execute("CREATE TRIGGER refuse_copy BEFORE INSERT ON copies BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.save(book(), 2000)
    assert db.conn.execute("SELECT count(*) FROM books").fetchone()[0] == 0
    db.conn.execute("DROP TRIGGER refuse_copy")
    db.save(book(), 2000)
    with pytest.raises(ValueError):
        db.save(book("不应保存的书名"), 2001, book_id=1, copy_id=999)
    assert db.book(1)["title"] == "测试图书"


def test_search_and_or_literal_wildcards_and_sort(db):
    db.save(book("Alpha 100%_", authors="甲", publisher="出版社甲", isbn="9787111544937"), 2000, "A1")
    db.save(book("Beta", authors="乙", publisher="出版社乙"), 2020, "B2")
    db.save(book("Alpha 100%_", authors="甲", publisher="出版社甲", isbn="9787111544937"), 2025, "A2", book_id=1)
    assert len(db.search(fields={"authors": "甲", "publisher": "乙"})) == 0
    assert len(db.search(fields={"authors": "甲", "publisher": "乙"}, mode="OR")) == 3
    assert len(db.search(quick="100%_")) == 2
    assert len(db.search(quick="' OR 1=1 --")) == 0
    assert len(db.search(fields={"isbn": "978-7-111-54493-7"}, exact_isbn=True)) == 2
    assert len(db.search(fields={"code": "I247.57-000001(1)"}, exact_code=True)) == 1
    assert len(db.search(year_min=2021, year_max=2026)) == 1
    assert [r["publication_year"] for r in db.search(sort="edition_year")] == [2025, 2000, 2020]
    assert [r["copy_no"] for r in db.search(sort="code", descending=True)] == [0, 1, 0]
    assert len(db.search(flags=["duplicates"])) == 2


def test_same_isbn_new_editions_allowed(db):
    db.save(book("版本一", isbn="9787111544937"), 2000)
    db.save(book("版本二", isbn="9787111544937"), 2001)
    assert len(db.find_isbn("9787111544937")) == 2


def test_backup_reset_restore_cover_and_classification(db, tmp_path):
    db.save(book(cover_blob=b"original-cover", cover_mime="image/jpeg"), 2020)
    db.save(book(cover_blob=b"original-cover", cover_mime="image/jpeg"), 2022, book_id=1)
    backup = db.reset_with_backup()
    assert db.stats() == (0, 0)
    assert len(db.classifications()) == 22
    restored = LibraryDB(backup)
    assert restored.book(1)["cover_blob"] == b"original-cover"
    assert restored.stats() == (1, 2)
    assert restored.save(restored.book(1), 2026, book_id=1)[1].endswith("(2)")
    restored.close()
    assert db.save(book(), 2020)[1].endswith("000001")


def test_failed_backup_aborts_reset(db, monkeypatch):
    db.save(book(), 2000)
    def fail(*_):
        raise OSError("disk full")
    monkeypatch.setattr(db, "backup", fail)
    with pytest.raises(OSError):
        db.reset_with_backup()
    assert db.stats() == (1, 1)


def test_never_overwrite_other_files_or_newer_schema(db, tmp_path):
    target = tmp_path / "valuable.sqlite3"
    target.write_bytes(b"precious")
    with pytest.raises(FileExistsError):
        db.backup(target)
    with pytest.raises(FileExistsError):
        LibraryDB(target, create=True)
    with pytest.raises(sqlite3.DatabaseError):
        LibraryDB(target)
    assert target.read_bytes() == b"precious"
    assert db.save(book(), 2000)
    other = tmp_path / "future.sqlite3"
    db.backup(other)
    conn = sqlite3.connect(other)
    conn.execute("PRAGMA user_version=99")
    conn.close()
    with pytest.raises(ValueError, match="版本"):
        LibraryDB(other)


def test_classification_import_atomic_and_cycle_check(db, tmp_path):
    path = tmp_path / "clc.csv"
    path.write_text("code,name,parent_code,source,edition\nTP,自动化与计算机,T,手工,未知\nTP303,计算机系统,TP,手工,未知\n", encoding="utf-8")
    assert db.import_classifications(path) == 2
    assert db.classifications("TP303")[0]["parent_code"] == "TP"
    path.write_text("code,name,parent_code,source,edition\nT,错误,TP,手工,未知\n", encoding="utf-8")
    with pytest.raises(ValueError, match="循环"):
        db.import_classifications(path)
    assert next(r for r in db.classifications() if r["code"] == "T")["parent_code"] == ""


@pytest.mark.parametrize("raw, expected", [("978-7-111-54493-7", "9787111544937"), ("0-306-40615-2", "9780306406157"), ("", "")])
def test_isbn(raw, expected):
    assert normalize_isbn(raw) == expected


@pytest.mark.parametrize("raw", ["9787111544930", "1234567890123", "0306406153", "abc"])
def test_invalid_isbn(raw):
    with pytest.raises(ValueError):
        normalize_isbn(raw)


def test_scan_gate_requires_departure_and_draft_release():
    gate = ScanGate()
    assert gate.observe("9787111544937")
    assert gate.observe("9787111544937") is None
    gate.release()
    assert gate.observe("9787111544937") is None
    for _ in range(3):
        gate.observe("")
    assert gate.observe("9787111544937")


def test_csv_unicode_and_formula_protection(db, tmp_path):
    db.save(book("=HYPERLINK(危险)"), 2020)
    path = tmp_path / "export.csv"
    db.export_csv(path, db.search())
    assert "'=HYPERLINK" in path.read_text(encoding="utf-8-sig")


def test_summary_search_combines_with_other_fields_and_returns_full_text(db):
    summary = "本书介绍深海考古与沉船，含 100%_ 原始资料。"
    db.save(book("海洋研究", authors="张三", summary=summary), 2020)
    db.save(book("另一书目", authors="李四", summary="普通介绍"), 2022)
    assert [r["title"] for r in db.search(quick="深海考古")] == ["海洋研究"]
    assert db.search(quick="100%_")[0]["summary"] == summary
    assert len(db.search(fields={"summary": "考古", "authors": "张三"})) == 1
    assert len(db.search(fields={"summary": "考古", "authors": "李四"})) == 0
    assert len(db.search(fields={"summary": "考古", "authors": "李四"}, mode="OR")) == 2
    assert not db.search(fields={"summary": "' OR 1=1 --"})
