import csv
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime

from .domain import BOOK_FIELDS, accession, normalize_isbn, validate_year

APP_ID = 0x504C4942
SCHEMA_VERSION = 1
MAJOR_CLASSES = {
    "A": "马克思主义、列宁主义、毛泽东思想、邓小平理论", "B": "哲学、宗教",
    "C": "社会科学总论", "D": "政治、法律", "E": "军事", "F": "经济",
    "G": "文化、科学、教育、体育", "H": "语言、文字", "I": "文学", "J": "艺术",
    "K": "历史、地理", "N": "自然科学总论", "O": "数理科学和化学", "P": "天文学、地球科学",
    "Q": "生物科学", "R": "医药、卫生", "S": "农业科学", "T": "工业技术",
    "U": "交通运输", "V": "航空、航天", "X": "环境科学、安全科学", "Z": "综合性图书",
}
CODE_SQL = "(COALESCE(NULLIF(b.clc_code,''),'PENDING') || '-' || printf('%06d',b.id) || CASE WHEN c.copy_no=0 THEN '' ELSE '(' || c.copy_no || ')' END)"
BASE_SQL = "COALESCE(NULLIF(b.clc_code,''),'PENDING') COLLATE NOCASE, b.id"
SEARCH_FIELDS = {
    "title": "b.title", "authors": "b.authors", "translators": "b.translators",
    "publisher": "b.publisher", "isbn": "b.isbn", "code": CODE_SQL,
    "clc_code": "b.clc_code", "location": "c.location", "notes": "c.notes", "summary": "b.summary",
}
SORTS = {
    "default": f"{BASE_SQL}, c.copy_no, c.publication_year DESC",
    "edition_year": f"{BASE_SQL}, c.publication_year DESC, c.copy_no",
    "code": f"{BASE_SQL}, c.copy_no", "title": "b.title COLLATE NOCASE",
    "authors": "b.authors", "translators": "b.translators", "publisher": "b.publisher",
    "publication_year": "c.publication_year", "clc_code": "b.clc_code",
    "location": "c.location", "created_at": "c.created_at", "copy_count": "copy_count",
    "summary": "b.summary COLLATE NOCASE",
}


def like(value):
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class LibraryDB:
    def __init__(self, path, *, create=False):
        self.path = Path(path).resolve()
        created = False
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("xb"):
                pass
            created = True
        elif not self.path.is_file():
            raise ValueError("数据库不存在，请选择现有库或新建库")
        try:
            self.conn = sqlite3.connect(self.path, timeout=5)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys=ON")
            if create:
                self._initialize()
            else:
                if self.conn.execute("PRAGMA application_id").fetchone()[0] != APP_ID:
                    raise ValueError("该文件不是个人藏书管理程序的数据库")
                if self.conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    raise ValueError("数据库版本不受此程序支持，请使用对应版本")
                if self.conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("数据库完整性检查失败，请恢复备份")
                self.conn.execute("SELECT isbn,next_copy_no FROM books LIMIT 0")
                self.conn.execute("SELECT publication_year FROM copies LIMIT 0")
                self.conn.execute("SELECT code FROM classifications LIMIT 0")
            self.conn.execute("PRAGMA journal_mode=DELETE")
            # Actually acquire a write transaction before switching from the old library.
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.rollback()
        except Exception:
            if hasattr(self, "conn"):
                self.conn.close()
            if created:
                self.path.unlink(missing_ok=True)
            raise

    def _initialize(self):
        self.conn.executescript(f"""
            BEGIN;
            PRAGMA application_id={APP_ID};
            PRAGMA user_version={SCHEMA_VERSION};
            CREATE TABLE books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                isbn TEXT NOT NULL DEFAULT '', title TEXT NOT NULL CHECK(length(trim(title))>0),
                authors TEXT NOT NULL DEFAULT '', translators TEXT NOT NULL DEFAULT '',
                publisher TEXT NOT NULL DEFAULT '', clc_code TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '', reference_pubdate TEXT NOT NULL DEFAULT '',
                cover_blob BLOB, cover_mime TEXT NOT NULL DEFAULT '', source_info TEXT NOT NULL DEFAULT '{{}}',
                next_copy_no INTEGER NOT NULL DEFAULT 0 CHECK(next_copy_no>=0),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE TABLE copies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                copy_no INTEGER NOT NULL CHECK(copy_no>=0),
                publication_year INTEGER NOT NULL CHECK(publication_year BETWEEN 1 AND 9999),
                location TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                UNIQUE(book_id,copy_no)
            );
            CREATE TABLE classifications (
                code TEXT PRIMARY KEY, name TEXT NOT NULL, parent_code TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '', edition TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX idx_isbn ON books(isbn);
            CREATE INDEX idx_clc ON books(clc_code);
            CREATE INDEX idx_year ON copies(publication_year);
            COMMIT;
        """)
        with self.conn:
            self.conn.executemany("INSERT INTO classifications(code,name,source,edition) VALUES(?,?,?,?)",
                                  [(c, n, "中图法基本大类简表", "简表") for c, n in MAJOR_CLASSES.items()])

    def close(self):
        self.conn.close()

    def stats(self):
        return tuple(self.conn.execute("SELECT COUNT(DISTINCT book_id),COUNT(*) FROM copies").fetchone())

    def book(self, book_id):
        row = self.conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if not row:
            raise ValueError("书目不存在")
        result = dict(row)
        result["source_info"] = json.loads(result["source_info"])
        return result

    def copy(self, copy_id):
        row = self.conn.execute("SELECT * FROM copies WHERE id=?", (copy_id,)).fetchone()
        if not row:
            raise ValueError("馆藏册不存在")
        return dict(row)

    def find_isbn(self, isbn):
        return [dict(r) for r in self.conn.execute(
            "SELECT id,title,authors,publisher,reference_pubdate FROM books WHERE isbn=? ORDER BY id",
            (normalize_isbn(isbn),))] if isbn else []

    def possible_duplicates(self, data):
        return [dict(r) for r in self.conn.execute(
            "SELECT id,title,authors,publisher FROM books WHERE title=? AND authors=? AND publisher=?",
            tuple(data.get(k, "") for k in ("title", "authors", "publisher")))]

    @staticmethod
    def _book_values(data):
        clean = {k: data.get(k, "") for k in BOOK_FIELDS}
        clean["isbn"] = normalize_isbn(clean["isbn"])
        clean["title"] = clean["title"].strip()
        clean["clc_code"] = clean["clc_code"].strip().upper()
        if not clean["title"]:
            raise ValueError("请填写书名")
        if len(clean["clc_code"]) > 80 or any(c.isspace() for c in clean["clc_code"]):
            raise ValueError("主分类号请填单个分类号，不能含空格")
        clean["cover_blob"] = data.get("cover_blob")
        clean["source_info"] = json.dumps(data.get("source_info", {}), ensure_ascii=False)
        return tuple(clean[k] for k in BOOK_FIELDS)

    def save(self, data, year, location="", notes="", *, book_id=None, copy_id=None):
        year = validate_year(year)
        values = self._book_values(data)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            if book_id is None:
                cur = self.conn.execute(f"INSERT INTO books({','.join(BOOK_FIELDS)}) VALUES({','.join('?' for _ in BOOK_FIELDS)})", values)
                book_id = cur.lastrowid
            else:
                cur = self.conn.execute(f"UPDATE books SET {','.join(k+'=?' for k in BOOK_FIELDS)},updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (*values, book_id))
                if cur.rowcount != 1:
                    raise ValueError("书目已不存在")
            if copy_id is None:
                copy_no = self.conn.execute("SELECT next_copy_no FROM books WHERE id=?", (book_id,)).fetchone()[0]
                cur = self.conn.execute("INSERT INTO copies(book_id,copy_no,publication_year,location,notes) VALUES(?,?,?,?,?)",
                                        (book_id, copy_no, year, location, notes))
                copy_id = cur.lastrowid
                self.conn.execute("UPDATE books SET next_copy_no=next_copy_no+1 WHERE id=?", (book_id,))
            else:
                cur = self.conn.execute("UPDATE copies SET publication_year=?,location=?,notes=? WHERE id=? AND book_id=?",
                                        (year, location, notes, copy_id, book_id))
                if cur.rowcount != 1:
                    raise ValueError("馆藏册已不存在或所属书目不匹配")
                copy_no = self.copy(copy_id)["copy_no"]
        return copy_id, accession(values[5], book_id, copy_no)

    def delete_copy(self, copy_id):
        with self.conn:
            self.conn.execute("DELETE FROM copies WHERE id=?", (copy_id,))
        # Keep the book and its counter so that a later copy never reuses a number.

    def search(self, quick="", fields=None, *, mode="AND", year_min=None, year_max=None,
               exact_isbn=False, exact_code=False, flags=(), sort="default", descending=False):
        clauses, values, advanced = [], [], []
        if quick.strip():
            columns = [SEARCH_FIELDS[k] for k in ("title", "authors", "translators", "publisher", "isbn", "code", "summary")]
            clauses.append("(" + " OR ".join(c + " LIKE ? ESCAPE '\\'" for c in columns) + ")")
            values.extend(["%" + like(quick.strip()) + "%"] * len(columns))
        for key, value in (fields or {}).items():
            if not value or key not in SEARCH_FIELDS:
                continue
            col = SEARCH_FIELDS[key]
            if (key == "isbn" and exact_isbn) or (key == "code" and exact_code):
                advanced.append(col + "=?")
                values.append(normalize_isbn(value) if key == "isbn" else value.strip())
            else:
                advanced.append(col + " LIKE ? ESCAPE '\\'")
                values.append(("" if key == "clc_code" else "%") + like(value.strip()) + "%")
        year_parts = []
        if year_min is not None:
            year_parts.append("c.publication_year>=?")
            values.append(validate_year(year_min))
        if year_max is not None:
            year_parts.append("c.publication_year<=?")
            values.append(validate_year(year_max))
        if year_parts:
            advanced.append("(" + " AND ".join(year_parts) + ")")
        if advanced:
            clauses.append("(" + (" OR " if mode == "OR" else " AND ").join(advanced) + ")")
        flag_sql = {"pending": "b.clc_code=''", "no_cover": "b.cover_blob IS NULL",
                    "duplicates": "(SELECT COUNT(*) FROM copies cc WHERE cc.book_id=b.id)>1"}
        clauses.extend(flag_sql[f] for f in flags if f in flag_sql)
        order = SORTS.get(sort, SORTS["default"])
        if descending and sort not in ("default", "edition_year"):
            if sort == "code":
                order = "COALESCE(NULLIF(b.clc_code,''),'PENDING') COLLATE NOCASE DESC, b.id DESC, c.copy_no DESC"
            else:
                order += " DESC"
        sql = f"""SELECT c.id,c.book_id,c.copy_no,{CODE_SQL} AS code,
             b.title,b.authors,b.translators,b.publisher,b.isbn,b.clc_code,b.summary,
             c.publication_year,c.location,c.notes,c.created_at,
             (SELECT COUNT(*) FROM copies cc WHERE cc.book_id=b.id) AS copy_count
             FROM copies c JOIN books b ON b.id=c.book_id"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY " + order + ",c.id", values)]

    def classifications(self, query=""):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM classifications WHERE code LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' ORDER BY code",
            ("%" + like(query) + "%", "%" + like(query) + "%"))]

    def import_classifications(self, path):
        with open(path, encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            required = {"code", "name", "parent_code", "source", "edition"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("分类 CSV 缺少 code、name、parent_code、source、edition 列")
            rows = []
            for row in reader:
                item = tuple((row.get(k) or "").strip() for k in ("code", "name", "parent_code", "source", "edition"))
                if not item[0] or not item[1]:
                    raise ValueError("分类号与名称不能为空")
                rows.append((item[0].upper(), item[1], item[2].upper(), item[3], item[4]))
        parent_map = {r["code"]: r["parent_code"] for r in self.classifications()}
        parent_map.update({r[0]: r[2] for r in rows})
        for start in parent_map:
            current, visited = start, set()
            while current in parent_map and current:
                if current in visited:
                    raise ValueError("分类字典存在循环父子关系")
                visited.add(current)
                current = parent_map[current]
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO classifications VALUES(?,?,?,?,?)", rows)
        return len(rows)

    def backup(self, destination):
        destination = Path(destination).resolve()
        if destination == self.path:
            raise ValueError("备份路径不能是当前数据库")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive reservation avoids accidental overwrite of any existing library.
        with destination.open("xb"):
            pass
        try:
            target = sqlite3.connect(destination)
            try:
                self.conn.backup(target)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("备份校验失败")
            finally:
                target.close()
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def reset_with_backup(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = self.backup(self.path.parent / "backups" / f"{self.path.stem}-{stamp}.sqlite3")
        with self.conn:
            self.conn.execute("DELETE FROM copies")
            self.conn.execute("DELETE FROM books")
            self.conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('books','copies')")
        return backup

    @staticmethod
    def export_csv(path, rows):
        fields = ["code", "title", "authors", "translators", "publisher", "isbn", "publication_year",
                  "clc_code", "location", "notes", "created_at", "copy_count"]
        with open(path, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            for row in rows:
                # Keep spreadsheet programs from treating user text as formulas.
                writer.writerow("'" + str(row[k]) if isinstance(row[k], str) and row[k].lstrip().startswith(("=", "+", "-", "@"))
                                else row[k] for k in fields)
