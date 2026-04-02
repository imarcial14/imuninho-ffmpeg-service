#!/usr/bin/env python3
from flask import Flask, request, jsonify
import subprocess, os, tempfile, requests, json, time, logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__)

DRIVE_FOLDER_ID = os.environ.get('DRIVE_FOLDER_ID', '')
SERVICE_ACCOUNT_JSON = os.environ.get('SERVICE_ACCOUNT_JSON', '')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY', '')

def get_access_token():
    import base64
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
    sa = json.loads(SERVICE_ACCOUNT_JSON)
    header = base64.urlsafe_b64encode(json.dumps({"alg":"RS256","typ":"JWT"}).encode()).rstrip(b'=').decode()
    now = int(time.time())
    payload = {"iss": sa['client_email'], "scope": "https://www.googleapis.com/auth/drive.file", "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 3600}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=').decode()
    private_key = serialization.load_pem_private_key(sa['private_key'].encode(), password=None, backend=default_backend())
    signing_input = f"{header}.{payload_b64}".encode()
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b'=').decode()
    jwt_token = f"{header}.{payload_b64}.{sig_b64}"
    resp = requests.post('https://oauth2.googleapis.com/token', data={'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': jwt_token})
    resp.raise_for_status()
    return resp.json()['access_token']

def download_file(url, dest_path):
    logger.info(f"Baixando: {url[:80]}...")
    if url.startswith('gs://'):
        url = url.replace('gs://', 'https://storage.googleapis.com/')
    headers = {}
    # Adiciona API Key para downloads do Google AI Studio
    if 'generativelanguage.googleapis.com' in url and GOOGLE_API_KEY:
        separator = '&' if '?' in url else '?'
        url = f"{url}{separator}key={GOOGLE_API_KEY}"
    resp = requests.get(url, headers=headers, stream=True, timeout=120)
    resp.raise_for_status()
    with open(dest_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    size = os.path.getsize(dest_path)
    logger.info(f"Download OK: {size} bytes")
    return size

def upload_to_drive(file_path, filename, folder_id, access_token):
    logger.info(f"Uploading {filename}...")
    metadata = json.dumps({'name': filename, 'parents': [folder_id]})
    with open(file_path, 'rb') as f:
        boundary = 'boundary_imuninho_ffmpeg'
        body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{metadata}\r\n--{boundary}\r\nContent-Type: video/mp4\r\n\r\n').encode() + f.read() + f'\r\n--{boundary}--'.encode()
    resp = requests.post(
        'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,webViewLink',
        headers={'Authorization': f'Bearer {access_token}', 'Content-Type': f'multipart/related; boundary={boundary}'},
        data=body, timeout=300
    )
    resp.raise_for_status()
    return resp.json()

@app.route('/health', methods=['GET'])
def health():
    result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
    return jsonify({'status': 'ok', 'ffmpeg': result.stdout.split('\n')[0] if result.returncode == 0 else 'not found'})

@app.route('/render', methods=['POST'])
def render():
    data = request.json
    if not data:
        return jsonify({'error': 'Body JSON obrigatório'}), 400
    required = ['roteiro_id', 'clip1_url', 'clip2_url', 'folder_id', 'filename']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Campos obrigatórios ausentes: {missing}'}), 400

    hook_text = data.get('hook', '')[:60].replace("'", "").replace('"', '').replace(':', '')
    cta_text = data.get('cta', '')[:60].replace("'", "").replace('"', '').replace(':', '')

    with tempfile.TemporaryDirectory() as tmpdir:
        clip1_path = os.path.join(tmpdir, 'clip1.mp4')
        clip2_path = os.path.join(tmpdir, 'clip2.mp4')
        output_path = os.path.join(tmpdir, data['filename'])
        try:
            download_file(data['clip1_url'], clip1_path)
            download_file(data['clip2_url'], clip2_path)

            music_path = None
            if data.get('music_url'):
                music_path = os.path.join(tmpdir, 'music.mp3')
                try:
                    download_file(data['music_url'], music_path)
                except Exception as e:
                    logger.warning(f"Trilha falhou: {e}")
                    music_path = None

            text_filter = (
                f"drawtext=text='{hook_text}':fontsize=42:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2:x=(w-text_w)/2:y=h*0.08:enable='between(t,0,4)'[v1];"
                f"[v1]drawtext=text='{cta_text}':fontsize=36:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2:x=(w-text_w)/2:y=h*0.85:enable='gte(t,12)'[vfinal]"
            )

            if music_path and os.path.exists(music_path):
                ffmpeg_cmd = ['ffmpeg', '-y', '-i', clip1_path, '-i', clip2_path, '-i', music_path,
                    '-filter_complex', f"[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout];[aout][2:a]amix=inputs=2:duration=first:weights=1 0.15[fout];[vout]{text_filter}",
                    '-map', '[vfinal]', '-map', '[fout]', '-c:v', 'libx264', '-crf', '23', '-preset', 'fast', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', output_path]
            else:
                ffmpeg_cmd = ['ffmpeg', '-y', '-i', clip1_path, '-i', clip2_path,
                    '-filter_complex', f"[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout];[vout]{text_filter}",
                    '-map', '[vfinal]', '-map', '[aout]', '-c:v', 'libx264', '-crf', '23', '-preset', 'fast', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', output_path]

            logger.info("Executando FFmpeg...")
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.error(f"FFmpeg stderr: {result.stderr[-2000:]}")
                return jsonify({'error': 'FFmpeg falhou', 'stderr': result.stderr[-1000:]}), 500

            output_size = os.path.getsize(output_path)
            logger.info(f"FFmpeg OK: {output_size} bytes")

            access_token = data.get('access_token') or get_access_token()
            drive_result = upload_to_drive(output_path, data['filename'], data['folder_id'], access_token)
            return jsonify({'success': True, 'roteiro_id': data['roteiro_id'], 'filename': data['filename'], 'file_id': drive_result.get('id'), 'drive_link': drive_result.get('webViewLink'), 'output_size_bytes': output_size})

        except requests.exceptions.RequestException as e:
            logger.error(f"Erro de rede: {e}")
            return jsonify({'error': f'Erro de rede: {str(e)}'}), 500
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'FFmpeg timeout (>600s)'}), 504
        except Exception as e:
            logger.error(f"Erro inesperado: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"FFmpeg Service iniciando na porta {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
