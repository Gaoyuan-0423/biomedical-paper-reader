"""Open exported decks, including notes, image references, source text and project isolation."""
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pptx import Presentation
from test_documents import fixture_pdf, main, TestClient


class FigureDeckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, path in [('DATA_DIR', self.root), ('PAPERS_DIR', self.root / 'papers')]:
            self.enterContext(patch.object(main, name, path))
        self.client = self.enterContext(TestClient(main.app))

    def paper(self, name):
        response = self.client.post('/api/papers', files={'file': (name+'.pdf',fixture_pdf(marker=name),'application/pdf')})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['id']

    def favorite(self, pid, doc='main'):
        path = '/api/papers/'+pid+'/figures'
        fig = next(f for f in self.client.get(path).json() if f['document_id']==doc)
        response = self.client.patch(path+'/'+fig['id'], json={'favorite':True})
        self.assertEqual(response.status_code,200,response.text)
        item = next(f for f in self.client.post('/api/figure-shelf/query',json={'paper_ids':[pid]}).json()['figures'] if f['id']==fig['id'])
        return {k:item[k] for k in ('paper_id','id','shelf_key')}

    def preview(self, ref, scope='all'):
        return self.client.post('/api/figure-decks/'+scope+'/preview',json=ref)

    def slide(self, ref, title):
        preview = self.preview(ref)
        self.assertEqual(preview.status_code,200,preview.text)
        return dict(**ref, revision=preview.json()['revision'], title=title, question='Which donors were paired?',
                    control='Independent controls', observation='A paired subset was reported.',
                    limitation='Small sample size', notes='讲稿中保留患者分母和研究局限。')

    def test_editable_text_order_original_images_source_and_real_notes(self):
        a,b=self.paper('First'),self.paper('Second')
        supplement=self.client.post('/api/papers/'+b+'/supplements', files={'file':('补图.pdf',fixture_pdf('Figure S2','Supp'),'application/pdf')}).json()['supplements'][0]
        first,second=self.favorite(a),self.favorite(b,supplement['id'])
        slides=[self.slide(second,'Second figure first'),self.slide(first,'First figure second')]
        response=self.client.post('/api/figure-decks/all/pptx',json={'slides':slides})
        self.assertEqual(response.status_code,200)
        self.assertIn('presentationml.presentation',response.headers['content-type'])
        pptx=Presentation(io.BytesIO(response.content))
        self.assertEqual(len(pptx.slides),2)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                if name.endswith(('.xml','.rels')): ET.fromstring(archive.read(name))
            for number,source in enumerate(slides,1):
                slide=pptx.slides[number-1]
                text={shape.name:shape.text for shape in slide.shapes if shape.has_text_frame}
                self.assertEqual(text['Title'],source['title'])
                self.assertEqual(text['Research question'],source['question'])
                self.assertEqual(text['Main observation'],source['observation'])
                self.assertIn('PDF p. 1',text['Source'])
                self.assertIn('补图.pdf' if number==1 else 'First.pdf',text['Source'])
                picture=next(shape for shape in slide.shapes if hasattr(shape,'image'))
                image=(main.paper_dir(source['paper_id'])/'figures'/(source['id']+'.png')).read_bytes()
                self.assertEqual(picture.image.blob,image)
                self.assertEqual(slide.notes_slide.notes_text_frame.text,source['notes'])
                self.assertIn('notesSlide',archive.read(f'ppt/slides/_rels/slide{number}.xml.rels').decode())
                self.assertIn(f'ppt/notesSlides/notesSlide{number}.xml',archive.namelist())

    def test_changed_sources_membership_and_unfilled_languages_rejected(self):
        a=self.paper('One')
        ref=self.favorite(a);slide=self.slide(ref,'Paired analysis')
        post=lambda scope,slides:self.client.post('/api/figure-decks/'+scope+'/pptx',json={'slides':slides})
        scope=self.client.post('/api/project-libraries',json={'name':'Other','paper_ids':[]}).json()['id']
        self.assertEqual(self.preview(ref,scope).status_code,409)
        self.assertEqual(post(scope,[slide]).status_code,409)
        for updates in [{'notes':'English only'}, {'title':'中文标题'}, {'control':'   '}]:
            self.assertEqual(post('all',[{**slide,**updates}]).status_code,422)
        self.assertEqual(post('all',[slide,slide]).status_code,422)
        self.client.patch('/api/papers/'+a+'/figures/'+ref['id'],json={'caption':'Updated after preview.'})
        self.assertEqual(post('all',[slide]).status_code,409)
        self.client.patch('/api/papers/'+a+'/figures/'+ref['id'],json={'favorite':False})
        self.assertEqual(self.preview(ref).status_code,409)


    def test_drafting_uses_only_selected_figure_and_cited_language_matched_fields(self):
        pid = self.paper('TargetSlide')
        self.paper('PrivateOtherPaper')
        ref = self.favorite(pid)
        self.client.patch('/api/papers/'+pid+'/figures/'+ref['id'],json={'caption':'Paired specimens were collected.'})
        body = {**ref,'revision':self.preview(ref).json()['revision'],'fields':['observation','limitation','notes'],
                'reader_material':'读者整理的配对设计。'}
        reply = json.dumps({
            'observation':{'value':'Paired specimens were collected.','source':'caption','quote':'Paired specimens'},
            'notes':{'value':'请在介绍配对研究时核对取样设计。','source':'reader','quote':'配对设计'},
            'limitation':{'value':'Missing intervention','source':'caption','quote':'No intervention was performed.'},
            'title':{'value':'Unrequested','source':'caption','quote':'Paired specimens'}
        },ensure_ascii=False)
        for vision in ('','vision-model'):
            configured={'base_url':'https://example.test/v1','api_key':'test','model':'text-model','vision_model':vision}
            with patch.object(main,'load_settings',return_value=configured), patch.object(main,'chat_once',new_callable=AsyncMock,return_value=reply) as model:
                res=self.client.post('/api/figure-decks/all/draft',json=body)
                self.assertEqual(res.status_code,200,res.text)
                self.assertEqual(set(res.json()['fields']),{'observation','notes'})
                self.assertEqual(res.json()['used_image'],bool(vision))
                content=model.await_args.args[3][1]['content']
                if vision:
                    self.assertEqual(len(content),2)
                    self.assertTrue(content[1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
                    content=content[0]['text']
                self.assertNotIn('PrivateOtherPaper',content)
                self.assertEqual(json.loads(content)['source_material']['reader'],body['reader_material'])
                self.assertEqual(model.await_args.args[2],vision or 'text-model')

    def test_draft_errors_and_source_changes_do_not_return_guesses(self):
        pid=self.paper('Change')
        ref=self.favorite(pid)
        body={**ref,'revision':self.preview(ref).json()['revision'],'fields':['control']}
        endpoint='/api/figure-decks/all/draft'
        with patch.object(main,'load_settings',return_value={}):
            self.assertEqual(self.client.post(endpoint,json=body).status_code,400)
        settings={'base_url':'https://example.test/v1','api_key':'test','model':'text-model'}
        with patch.object(main,'load_settings',return_value=settings):
            for raw,expected in [('not json',502),
                                 (json.dumps({'control':{'value':'完全是中文','source':'caption','quote':'Figure'}}),200)]:
                with patch.object(main,'chat_once',new_callable=AsyncMock,return_value=raw):
                    result=self.client.post(endpoint,json=body)
                    self.assertEqual(result.status_code,expected,result.text)
                    if expected==200:self.assertEqual(result.json()['fields'],{})
            with patch.object(main,'chat_once',new_callable=AsyncMock,side_effect=TimeoutError()):
                self.assertEqual(self.client.post(endpoint,json=body).status_code,504)
            async def late(*args,**kwargs):
                directory=main.paper_dir(pid);figures=main.load_figures(directory)
                figures[0]['caption']='New material after the request'
                main.save_figures(directory,figures)
                return '{}'
            with patch.object(main,'chat_once',side_effect=late):
                self.assertEqual(self.client.post(endpoint,json=body).status_code,409)
