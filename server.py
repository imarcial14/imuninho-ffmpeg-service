#!/usr/bin/env python3
from flask import Flask, request, jsonify
import subprocess, os, tempfile, requests, json, base64, logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__)
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY', '')
def download_file(url, dest_path):
    logger.info(f"Baixando: {url[:80]}...")
    if url.startswith('gs://'):
        url = url.replace('gs://', 'https://storage.googleapis.com/')
    if 'generativelanguage.googleapis.com' in url and GOOGLE_API_KEY:
        sep = '&' if '?' in url else '?'
        url = f"{url}{sep}key={GOOGLE_API_KEY}"
    resp = requests.get(url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(dest_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    logger.info(f"Download OK: {os.path.getsize(dest_path)} bytes")
@app.route('/health')
def health():
    r = subprocess.run(['ffmpeg','-version'], capture_output=True, text=True)
    return jsonify({'status':'ok','ffmpeg':r.stdout.split('\n')[0]})
@app.route('/render', methods=['POST'])
def render():
    data = request.json
    if not data:
        return jsonify({'error':'Body obrigatorio'}), 400
    for field in ['roteiro_id','clip1_url','clip2_url','filename']:
        if not data.get(field):
            return jsonify({'error':f'Campo ausente: {field}'}), 400
    def san(t):
        return (t or '')[:60].replace("'",' ').replace('"',' ').replace(':',' ').replace('\\',' ').replace('%','pct').replace('\n',' ')
    hook = san(data.get('hook',''))
    cta = san(data.get('cta',''))
    font = '/usr/share/fonts/ttf-dejavu/DejaVuSans-Bold.ttf'
    with tempfile.TemporaryDirectory() as d:
        c1 = os.path.join(d,'c1.mp4')
        c2 = os.path.join(d,'c2.mp4')
        out = os.path.join(d, data['filename'])
        try:
            download_file(data['clip1_url'], c1)
            download_file(data['clip2_url'], c2)
            tf = (
                f"drawtext=fontfile={font}:text='{hook}':fontsize=42:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2:x=(w-text_w)/2:y=h*0.08:enable='between(t\\,0\\,4)'[v1];"
                f"[v1]drawtext=fontfile={font}:text='{cta}':fontsize=36:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2:x=(w-text_w)/2:y=h*0.85:enable='gte(t\\,12)'[vf]"
            )
            cmd = ['ffmpeg','-y','-i',c1,'-i',c2,
                '-filter_complex',f"[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vo][ao];[vo]{tf}",
                '-map','[vf]','-map','[ao]',
                '-c:v','libx264','-crf','23','-preset','fast',
                '-c:a','aac','-b:a','128k','-movflags','+faststart',out]
            logger.info("FFmpeg iniciando...")
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                logger.error(r.stderr[-2000:])
                return jsonify({'error':'FFmpeg falhou','stderr':r.stderr[-500:]}), 500
            logger.info(f"FFmpeg OK: {os.path.getsize(out)} bytes")
            with open(out,'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            return jsonify({'success':True,'roteiro_id':data['roteiro_id'],'filename':data['filename'],'video_base64':b64,'size':os.path.getsize(out)})
        except Exception as e:
            logger.error(str(e), exc_info=True)
            return jsonify({'error':str(e)}), 500
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',8080)))
