import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3

import cv2
import numpy as np
import pytest
import zxingcpp
from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtGui import QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from app.database import LibraryDB, APP_ID
from app.domain import publication_barcode
from app.i18n import default_language, LANGUAGES, set_language
from app.dialogs import CoverDialog, CornerEditor, CoverViewer
from app.licenses import LicenseDialog
from app.imaging import encode_cover, detect_cover_corners
from test_ui import application, window


def old_library(path):
    conn = sqlite3.connect(path)
    conn.executescript(f"""
      PRAGMA application_id={APP_ID}; PRAGMA user_version=1;
      CREATE TABLE books(id INTEGER PRIMARY KEY AUTOINCREMENT,isbn TEXT DEFAULT '',title TEXT,
        authors TEXT DEFAULT '',translators TEXT DEFAULT '',publisher TEXT DEFAULT '',clc_code TEXT DEFAULT '',
        summary TEXT DEFAULT '',reference_pubdate TEXT DEFAULT '',cover_blob BLOB,cover_mime TEXT DEFAULT '',
        source_info TEXT DEFAULT '{{}}',next_copy_no INTEGER DEFAULT 0,created_at TEXT DEFAULT '',updated_at TEXT DEFAULT '');
      CREATE TABLE copies(id INTEGER PRIMARY KEY AUTOINCREMENT,book_id INTEGER,copy_no INTEGER,
        publication_year INTEGER,location TEXT DEFAULT '',notes TEXT DEFAULT '',created_at TEXT DEFAULT '',UNIQUE(book_id,copy_no));
      CREATE TABLE classifications(code TEXT PRIMARY KEY,name TEXT,parent_code TEXT DEFAULT '',source TEXT DEFAULT '',edition TEXT DEFAULT '');
      INSERT INTO books(title,clc_code,cover_blob) VALUES('First','TP303',X'010203'),('Second','I247.57',NULL),('Third','TP303',NULL);
      UPDATE books SET next_copy_no=1;
      INSERT INTO copies(book_id,copy_no,publication_year,location) VALUES(1,0,2000,'Shelf A'),(2,0,2001,'Shelf B'),(3,0,2002,'Shelf C');
    """)
    conn.close()


def test_v1_migration_backs_up_data_and_allocates_per_class(tmp_path):
    path = tmp_path/'old.sqlite3'
    old_library(path)
    original = sqlite3.connect(path)
    original_dump = list(original.iterdump())
    original.close()
    db = LibraryDB(path)
    try:
        backup = sqlite3.connect(db.migration_backup)
        assert list(backup.iterdump()) == original_dump
        assert backup.execute('PRAGMA user_version').fetchone()[0] == 1
        assert backup.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        backup.close()
        assert [r['code'] for r in db.search()] == ['I247.57-001','TP303-001','TP303-002']
        assert db.book(1)['cover_blob'] == b'\x01\x02\x03'
        assert db.copy(1)['location'] == 'Shelf A'
        assert db.copy(1)['reading_status'] == 'unread'
        assert db.conn.execute('SELECT old_code,new_code FROM legacy_codes WHERE copy_id=3').fetchone()[:] == ('TP303-000003','TP303-002')
        assert db.save({'title':'Fourth','clc_code':'TP303'},2003)[1] == 'TP303-003'
        second = db.book(2)
        second['clc_code'] = 'TP303'
        assert db.save(second,2001,book_id=2,copy_id=2)[1] == 'TP303-004'
        second['clc_code'] = 'I247.57'
        assert db.save(second,2001,book_id=2,copy_id=2)[1] == 'I247.57-002'
    finally:
        db.close()
    reopened = LibraryDB(path)
    assert reopened.migration_backup is None
    reopened.close()


def test_failed_migration_backup_leaves_old_database_unchanged(tmp_path,monkeypatch):
    path = tmp_path/'old.sqlite3'
    old_library(path)
    before = path.read_bytes()
    def fail(*args):
        raise OSError('full disk')
    monkeypatch.setattr(LibraryDB,'backup',fail)
    with pytest.raises(OSError):
        LibraryDB(path)
    assert path.read_bytes() == before


def test_magazine_barcode_addons_are_read_and_price_is_not_an_issue():
    image = np.asarray(zxingcpp.write_barcode_to_image(zxingcpp.create_barcode('9770174736005 03',zxingcpp.BarcodeFormat.EAN13),scale=4,add_hrt=False))
    codes = zxingcpp.read_barcodes(image,ean_add_on_symbol=zxingcpp.EanAddOnSymbol.Read)
    record = publication_barcode(max(codes,key=lambda item:len(item.text)).text)
    assert record['issn']=='0174-7363' and record['issue_number']=='03'
    assert publication_barcode('9770174736005')['issue_number']==''
    assert publication_barcode('9770174736005 26003')['issue_number']==''
    assert publication_barcode('9770174736005 26003')['barcode_addon']=='26003'
    assert publication_barcode('9787111544937 59000')['media_type']=='book'
    with pytest.raises(ValueError):
        publication_barcode('9770174736004 03')


def test_magazine_entry_and_per_copy_reading_state(window,application):
    window.entry.apply_barcode(publication_barcode('9770174736005 03'))
    assert not window.entry.fields['authors'].isVisible()
    assert not window.entry.fields['summary'].isVisible()
    window.entry.set_text('title','Test Journal')
    window.entry.set_text('publication_year','2026')
    window.entry.reading_status.setCurrentIndex(1)
    window.save_entry()
    row=window.db.search()[0]
    assert (row['media_type'],row['issue_year'],row['issue_number'],row['reading_status'],row['code']) == ('magazine',2026,'03','reading','MAG-001')
    assert not row['authors'] and not row['summary'] and not row['clc_code']
    book=window.db.book(row['book_id'])
    window.db.save(book,2026,book_id=book['id'],reading_status='read')
    assert len(window.db.search(reading_status='read',media_type='magazine'))==1
    assert window.db.search(reading_status='reading')[0]['id']==row['id']


@pytest.mark.parametrize('locale,expected',[('zh-Hans-CN','zh_CN'),('zh_CN','zh_CN'),('zh-SG','zh_CN'),('zh-Hant-TW','en'),('zh-HK','en'),('en-US','en'),('fr-FR','en'),('ru-RU','en'),('ar-SA','en')])
def test_default_language_only_simplified_chinese(locale,expected):
    assert default_language([locale])==expected


@pytest.mark.parametrize('language',list(LANGUAGES))
def test_language_switch_keeps_draft_and_filters_sources(window,application,language):
    window.entry.set_text('title','书名保留 / Original title')
    window.entry.set_text('location','客厅 A')
    window.settings_page.language.setCurrentIndex(window.settings_page.language.findData(language))
    application.processEvents()
    assert window.entry.text('title')=='书名保留 / Original title'
    assert window.entry.text('location')=='客厅 A'
    sources=[window.settings_page.source.itemData(i) for i in range(window.settings_page.source.count())]
    assert ('nlc' in sources) == (language=='zh_CN')
    assert application.layoutDirection() == (Qt.LayoutDirection.RightToLeft if language=='ar' else Qt.LayoutDirection.LeftToRight)
    if language!='zh_CN':
        assert not any('\u4e00'<=c<='\u9fff' for c in window.tabs.tabText(0))
    if language=='ar':
        text=QTextLayout('المكتبة الشخصية',application.font())
        text.beginLayout();text.createLine();text.endLayout()
        assert all(all(run.glyphIndexes()) for run in text.glyphRuns())
    window.set_language('zh_CN')


@pytest.mark.parametrize('language',['en','fr','es','ru','ar'])
def test_international_lookup_never_uses_nlc(monkeypatch,language):
    from app import edition
    from app.providers import catalog
    calls=[]
    async def forbidden(*args):
        raise AssertionError('NLC must not run')
    async def allowed(*args):
        calls.append('openlibrary')
        return []
    monkeypatch.setattr(edition,'OFFLINE',False)
    monkeypatch.setattr(catalog,'nlc',forbidden)
    monkeypatch.setattr(catalog,'openlibrary',allowed)
    asyncio.run(edition.lookup('9787111544937',source='nlc',detail_url='https://opac.nlc.cn/fake',language=language))
    assert calls==['openlibrary']


def test_camera_switches_to_cover_mode_and_stop_message_clears(window,application,monkeypatch):
    import app.window as window_module
    class Camera(QObject):
        barcode=Signal(str)
        message=Signal(str)
        focus_changed=Signal(object)
        finished=Signal()
        def __init__(self,*args):
            super().__init__();self.scanning=True;self.running=False
        def set_zoom(self,*args): pass
        def set_autofocus(self,*args): pass
        def start(self): self.running=True
        def isRunning(self): return self.running
        def latest(self): return None
        def requestInterruption(self):
            self.running=False
            QTimer.singleShot(0,self.finished.emit)
    monkeypatch.setattr(window_module,'CameraWorker',Camera)
    monkeypatch.setattr(window,'lookup_isbn',lambda:None)
    window.toggle_camera()
    window.on_barcode('9787111544937')
    assert window.camera_mode=='cover' and not window.camera.scanning
    window.tabs.setCurrentIndex(3);window.tabs.setCurrentIndex(0)
    assert not window.camera.scanning
    window.reset_entry()
    assert window.camera.scanning
    window.toggle_camera()
    application.processEvents()
    assert window.camera is None and window.entry.camera_button.isEnabled()
    assert '已关闭' in window.entry.camera_status.text()


def test_space_captures_without_toggling_autofocus(window,application,monkeypatch):
    dialog=CoverDialog(window)
    calls=[]
    monkeypatch.setattr(dialog,'capture',lambda:calls.append('capture'))
    dialog.show();dialog.activateWindow();application.processEvents()
    focus=dialog.camera_controls.autofocus
    focus.setFocus();before=focus.isChecked()
    QTest.keyClick(focus,Qt.Key.Key_Space)
    assert calls==['capture'] and focus.isChecked()==before
    dialog.reject()


def test_corner_editor_live_coordinates_and_cancel_preserve_input(window,application):
    frame=np.zeros((600,800,3),np.uint8)
    points=np.array([[100,100],[700,100],[700,500],[100,500]],np.float32)
    editor=CornerEditor(frame,points,window)
    editor.show();application.processEvents();editor.view.setFocus()
    QTest.keyClick(editor.view,Qt.Key.Key_Right)
    assert editor.view.points[0,0]==101
    assert '101.0' in editor.coordinates.text()
    assert points[0,0]==100
    editor.reject()


def test_cover_viewer_and_full_license_view(window,application):
    frame=np.zeros((200,150,3),np.uint8)
    viewer=CoverViewer(encode_cover(frame),'Cover',window)
    viewer.zoom.setValue(200)
    assert viewer.picture.width()==300
    viewer.accept()
    licenses=LicenseDialog(window)
    assert any(label=='LICENSE' for label,_ in licenses.documents)
    licenses.list.setCurrentRow(next(i for i,(label,_) in enumerate(licenses.visible) if label=='LICENSE'))
    assert 'PolyForm' in licenses.text.toPlainText()
    assert any('WenKai-OFL' in label for label,_ in licenses.documents)
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not window.windowIcon().isNull()
    licenses.reject()


def test_integrated_boundary_model_on_real_local_samples():
    root=Path(__file__).parent/'fixtures/book_boundary'
    if not (root/'annotations.json').is_file():
        pytest.skip('Private local camera fixtures are not distributed with the source')
    annotations=json.loads((root/'annotations.json').read_text('utf-8'))
    from app.boundary import polygon_iou
    for item in annotations['images']:
        image=cv2.imdecode(np.frombuffer((root/item['file']).read_bytes(),np.uint8),cv2.IMREAD_COLOR)
        points,found=detect_cover_corners(image)
        assert found and polygon_iou(points,np.array(item['corners'],np.float32)) > .95
