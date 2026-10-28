"""Publisher uses real local HTTP fixtures; never creates a real GitHub release."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import build_release
import publish_release

REVISION='1'*40
TOKEN='fictional-publisher-token'


class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,code,value):
        raw=json.dumps(value).encode();self.send_response(code);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        self.server.events.append(('GET',self.path));self.send(200 if self.server.exists else 404,{'tag_name':'v0.2.0'})
    def do_POST(self):
        self.server.events.append(('POST',self.path))
        if self.headers.get('Authorization')!='Bearer '+TOKEN:self.send(401,{});return
        raw=self.rfile.read(int(self.headers['Content-Length']))
        if self.path.startswith('/upload?'):
            self.server.uploads+=1
            self.send(201,{'size':len(raw),'digest':'sha256:'+('0'*64 if self.server.corrupt else hashlib.sha256(raw).hexdigest())})
        else:
            self.server.release=json.loads(raw)
            self.send(201,{'id':1,'upload_url':f'http://127.0.0.1:{self.server.server_address[1]}/upload{{?name,label}}'})
    def do_PATCH(self):
        self.server.events.append(('PATCH',self.path));self.server.publish=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.send(200,{'draft':False,'tag_name':'v0.2.0','html_url':'https://fixture.invalid/release'})


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        # Test-only clean revision fixture, never a publishable production checkout.
        def git(args,**kwargs):
            return REVISION+'\n' if args[1]=='rev-parse' else '1700000000\n' if args[1]=='show' else '' if args[1]=='status' else original_git(args,**kwargs)
        original_git=build_release.subprocess.check_output
        with patch.object(build_release.subprocess,'check_output',side_effect=git):build_release.build(self.root/'artifacts',allow_dirty=True)
        self.notes=self.root/'notes';self.notes.write_text('Test fixture only.')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        self.server.events=[];self.server.exists=False;self.server.corrupt=False;self.server.uploads=0
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.base=f'http://127.0.0.1:{self.server.server_address[1]}'
        self.environment={'GITHUB_REPOSITORY':'Kordeone/khroxy','GITHUB_REF':'refs/tags/v0.2.0','GITHUB_SHA':REVISION,'GITHUB_TOKEN':TOKEN}
    def publish(self):return publish_release.publish(self.root/'artifacts',self.notes,self.environment,self.base)
    def test_verified_assets_publish_only_after_all_uploads(self):
        result=self.publish();self.assertEqual(result['assets_verified'],3)
        self.assertTrue(self.server.release['draft']);self.assertFalse(self.server.publish['draft'])
        self.assertEqual(self.server.uploads,3);self.assertEqual(self.server.events[-1][0],'PATCH')
    def test_corrupt_uploaded_digest_leaves_unpublished_draft(self):
        self.server.corrupt=True
        with self.assertRaisesRegex(ValueError,'unpublished draft'):self.publish()
        self.assertFalse(any(event[0]=='PATCH' for event in self.server.events))
    def test_existing_release_is_never_replaced(self):
        self.server.exists=True
        with self.assertRaisesRegex(ValueError,'already exists'):self.publish()
        self.assertTrue(all(event[0]=='GET' for event in self.server.events))
    def test_wrong_context_or_commit_fails_before_network(self):
        for key,value in (('GITHUB_REPOSITORY','foreign/repository'),('GITHUB_REF','refs/heads/main'),('GITHUB_SHA','2'*40)):
            environment=dict(self.environment,**{key:value})
            with self.assertRaises(ValueError):publish_release.publish(self.root/'artifacts',self.notes,environment,self.base)
        self.assertEqual(self.server.events,[])

if __name__=='__main__':unittest.main()
