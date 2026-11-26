"""Cache exact-ABI primary sources and metadata with bounded redacted diagnostics."""
from concurrent.futures import ThreadPoolExecutor
import base64
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import time
import traceback
import urllib.request
import uuid

HERE=Path(__file__).resolve().parent/'chrome-bridge'
TAG='158.0.8127.37'
START=time.monotonic()
REPORT={'operation':'fetch_chrome_bridge_exact_abi_sources','attempt':uuid.uuid4().hex[:16],
        'source_tag':TAG,'selection':'installed_binary_compatibility_fixture','events':[]}
PATHS=['.gn','chrome/VERSION','content/common/child_process.mojom',
       'content/common/content_constants_internal.h',
       'media/mojo/mojom/content_decryption_module.mojom','media/mojo/mojom/decryptor.mojom',
       'media/mojo/mojom/cdm_service.mojom','media/mojo/services/mojo_cdm_service.cc',
       'media/mojo/mojom/frame_interface_factory.mojom','media/mojo/services/mojo_cdm_helper.cc',
       'media/mojo/mojom/BUILD.gn','media/media_options.gni',
       'url/mojom/origin.mojom','media/base/cdm_factory.h','media/cdm/cdm_adapter_factory.cc',
       'media/base/eme_constants.h','media/base/content_decryption_module.h',
       'media/mojo/mojom/output_protection.mojom','media/mojo/mojom/cdm_document_service.mojom',
       'media/mojo/mojom/media_types.mojom','media/base/video_codecs.h',
       'ui/gfx/geometry/mojom/geometry.mojom','ui/gfx/mojom/hdr_metadata.mojom',
       'ui/gfx/mojom/color_space.mojom','media/mojo/mojom/video_decoder_config_mojom_traits.cc',
       'media/base/video_color_space.h','media/cdm/cdm_helpers.cc',
       'media/formats/mp4/mp4_stream_parser.cc',
       'media/mojo/services/cdm_service.cc',
       'media/mojo/services/mojo_decryptor_service.cc','chrome/common/media/cdm_host_file_path.cc',
       'content/browser/service_host/utility_sandbox_delegate_win.cc',
       'content/browser/media/service_factory.cc','content/public/browser/service_process_host.h',
       'docs/design/sandbox.md','content/utility/services.cc']

def fetch(path):
    begin=time.monotonic();request_id=uuid.uuid4().hex[:12]
    url='https://chromium.googlesource.com/chromium/src/+/refs/tags/'+TAG+'/'+path+'?format=TEXT'
    event={'stage':'source_fetch','path':path,'request_id':request_id,'url':url}
    try:
        file=HERE/('fixture158__'+path.replace('/','__'))
        if file.is_file():
            source=file.read_bytes()
            return dict(event,result='success',from_local_exact_tag_cache=True,bytes=len(source),
                        sha256=hashlib.sha256(source).hexdigest(),file=file.name,elapsed_ms=0)
        with urllib.request.urlopen(url,timeout=25) as response:
            wire=response.read(2*1024*1024+1)
            if len(wire)>2*1024*1024:raise ValueError('Public source size limit')
            source=base64.b64decode(wire,validate=True)
            file.write_bytes(source)
            event.update(result='success',http_status=response.status,bytes=len(source),
                         sha256=hashlib.sha256(source).hexdigest(),file=file.name)
    except Exception as error:
        event.update(result='failure',http_status=getattr(error,'code',None),exception={
            'type':type(error).__name__,'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                for f in traceback.extract_tb(error.__traceback__)]})
    event['elapsed_ms']=round((time.monotonic()-begin)*1000)
    return event

if __name__=='__main__':
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            REPORT['events']=list(pool.map(fetch,PATHS))
        url='https://chromium.googlesource.com/chromium/src/+/refs/tags/'+TAG+'?format=JSON'
        begin=time.monotonic()
        with urllib.request.urlopen(url,timeout=25) as response:
            raw=response.read(256*1024)
            info=json.loads(raw[raw.index(b'{'):])
            # Commit metadata is public; omit people/emails and the commit message.
            stamp=info['committer']['time']
            try:parsed=datetime.strptime(stamp,'%a %b %d %H:%M:%S %Y %z')
            except ValueError:parsed=datetime.strptime(stamp,'%a %b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc)
            timestamp=int(parsed.timestamp())
            build=HERE/'client-sdk/src/build/util'
            (build/'LASTCHANGE.committime').write_text(str(timestamp)+'\n')
            (build/'LASTCHANGE').write_text('LASTCHANGE='+info['commit']+'\n')
            REPORT['commit_metadata']={'http_status':response.status,'url':url,'commit':info['commit'],
                'gitiles_timestamp':stamp,'unix_timestamp':timestamp,
                'timestamp_timezone_note':'Gitiles timestamps without an offset are treated as UTC for build metadata only',
                'elapsed_ms':round((time.monotonic()-begin)*1000)}
        REPORT['result']='success' if all(e['result']=='success' for e in REPORT['events']) else 'partial_failure'
    except Exception as error:
        REPORT.update(result='failure',exception={'type':type(error).__name__,
            'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                for f in traceback.extract_tb(error.__traceback__)]})
    finally:
        REPORT['elapsed_ms']=round((time.monotonic()-START)*1000)
        (HERE/'bridge-exact-source-audit.json').write_text(json.dumps(REPORT,indent=2))
        print(json.dumps({'result':REPORT.get('result'),'events':[{k:e.get(k) for k in
            ('path','result','http_status','bytes','exception')} for e in REPORT['events']],
            'exception':REPORT.get('exception'),'elapsed_ms':REPORT['elapsed_ms']}))
