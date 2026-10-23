import asyncio
from contextlib import asynccontextmanager, closing
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'papermind'))
import cloud_files as files
import cloud_figures as figures
from src import database


class FigureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'library.db'
        for scope in (patch.object(database,'DB_PATH',self.path),patch.dict(os.environ,{'PAPERMIND_SQL_BACKEND':'sqlite'})):
            scope.start()
            self.addCleanup(scope.stop)
        database.init_db()
        with closing(database._ensure_db()) as conn:
            conn.executescript(files.SCHEMA+figures.SCHEMA)
        self.paper = database.save_paper({'title':'Synthetic figure'},'alice')
        buffer = io.BytesIO()
        Image.new('RGB',(8,8),'navy').save(buffer,format='PNG')
        self.png = buffer.getvalue()

    def reserve(self):
        return figures.reserve(self.paper,'alice',len(self.png),'image/png','results',1,'Synthetic figure')

    def finish(self,row,data=None,hook=None):
        content = self.png if data is None else data
        class Source:
            metadata = SimpleNamespace(content_type='image/png')
            def __aiter__(self):
                async def chunks():
                    if hook: hook()
                    yield content
                return chunks()
        @asynccontextmanager
        async def stream(*_,**__):
            yield Source()
        with patch('vercel.blob.stream_blob',stream):
            return asyncio.run(figures.complete(self.paper,'alice',row['id']))

    def test_reservation_deduplicates_and_checks_owner(self):
        row = self.reserve()
        self.assertEqual(row['id'],self.reserve()['id'])
        self.assertTrue(figures.valid_path(row))
        with self.assertRaises(HTTPException): figures.reserve(self.paper,'bob',len(self.png),'image/png','results')

    def test_verified_image_and_board_item_commit_once(self):
        row = self.reserve()
        result = self.finish(row)
        second = self.finish(row,b'unused')
        self.assertEqual(result['item']['id'],second['item']['id'])
        self.assertEqual(len(database.get_board_items(self.paper)),1)
        self.assertEqual(figures.current(self.paper,'alice',row['name'])['sha256'],result['sha256'])
        with self.assertRaises(HTTPException): figures.current(self.paper,'bob',row['name'])

    def test_invalid_image_does_not_create_board_item(self):
        row = self.reserve()
        with self.assertRaises(HTTPException): self.finish(row,b'x'*len(self.png))
        self.assertEqual(database.get_board_items(self.paper),[])

    def test_retirement_during_verification_cannot_resurrect(self):
        row = self.reserve()
        def retire():
            with files.transaction() as conn:
                conn.execute("UPDATE figure_uploads SET state='retired' WHERE id=?",(row['id'],))
        with self.assertRaises(HTTPException): self.finish(row,hook=retire)
        self.assertEqual(database.get_board_items(self.paper),[])

    def test_delete_item_revokes_read_and_late_completion(self):
        row = self.reserve()
        result = self.finish(row)
        with self.assertRaises(HTTPException): figures.retire_item(result['item']['id'],'bob')
        figures.retire_item(result['item']['id'],'alice')
        with self.assertRaises(HTTPException): figures.current(self.paper,'alice',row['name'])
        with self.assertRaises(HTTPException): self.finish(row)
        self.assertEqual(database.get_board_items(self.paper),[])

    def test_pdf_and_image_share_storage_quota(self):
        row = self.reserve()
        with files.transaction() as conn:
            conn.execute('UPDATE figure_uploads SET size=? WHERE id=?',(100*1024*1024,row['id']))
        with self.assertRaises(HTTPException): files.reserve(self.paper,'alice',10)
        with self.assertRaises(HTTPException): figures.reserve(self.paper,'alice',20,'image/png','methods')

    def test_cleanup_requires_grace_and_confirmed_absence(self):
        from vercel.blob import BlobNotFoundError
        row = self.reserve()
        delete = AsyncMock()
        head = AsyncMock(side_effect=BlobNotFoundError('absent'))
        with patch('vercel.blob.delete_blob',delete),patch('vercel.blob.head_blob',head):
            self.assertEqual(asyncio.run(figures.cleanup('alice'))['figures_reclaimed'],0)
            delete.assert_not_called()
            with files.transaction() as conn:
                conn.execute('UPDATE figure_uploads SET expires=0 WHERE id=?',(row['id'],))
            delete.side_effect = ValueError('private error')
            self.assertEqual(asyncio.run(figures.cleanup('alice'))['figures_cleanup_failed'],1)
            delete.side_effect = None
            self.assertEqual(asyncio.run(figures.cleanup('alice'))['figures_reclaimed'],1)

    def test_corrupt_ledger_path_is_never_sent_to_blob(self):
        row = self.reserve()
        with files.transaction() as conn:
            conn.execute("UPDATE figure_uploads SET pathname='other/private.png',expires=0 WHERE id=?",(row['id'],))
        with patch('vercel.blob.delete_blob',AsyncMock()) as delete:
            self.assertEqual(asyncio.run(figures.cleanup('alice'))['figures_cleanup_failed'],1)
            delete.assert_not_called()

    def test_exports_load_bounded_owned_images(self):
        row = self.reserve()
        self.finish(row)
        items = database.get_board_items(self.paper)
        with patch.object(figures,'read_bytes',AsyncMock(return_value=self.png)):
            self.assertEqual(figures.export_images(self.paper,'alice',items)[row['name']],self.png)
            with self.assertRaises(HTTPException): figures.export_images(self.paper,'bob',items)
        with files.transaction() as conn:
            conn.execute('UPDATE figure_uploads SET size=? WHERE id=?',(figures.MAX_EXPORT_IMAGES+1,row['id']))
        with self.assertRaises(HTTPException): figures.export_images(self.paper,'alice',items)

    def test_http_figure_download_and_exports(self):
        import api
        from fastapi.testclient import TestClient
        row = self.reserve()
        result = self.finish(row)
        with patch.dict(os.environ,{'PAPERMIND_FILE_BACKEND':'vercel-blob'}), \
             patch.object(api.account_auth,'identity',return_value={'id':'test','data_uid':'alice'}), \
             patch.object(api,'_get_user_id',return_value='alice'), \
             patch.object(figures,'read_bytes',AsyncMock(return_value=self.png)), \
             TestClient(api.app) as client:
            response = client.get(f'/api/board/{self.paper}/figures/'+row['name'])
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.content,self.png)
            response = client.get(f'/api/board/{self.paper}/export/marp')
            self.assertEqual(response.status_code,200)
            self.assertIn('data:image/png;base64,',response.text)
            response = client.get(f'/api/board/{self.paper}/export/pptx')
            self.assertEqual(response.status_code,200)
            from zipfile import ZipFile
            with ZipFile(io.BytesIO(response.content)) as archive:
                images = [name for name in archive.namelist() if name.startswith('ppt/media/')]
                self.assertEqual(len(images),1)
                self.assertEqual(archive.read(images[0]),self.png)
            self.assertEqual(client.delete('/api/board/items/'+str(result['item']['id'])).status_code,200)
