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
        separator = '&' if '?' in url else '?'
        url = f"{url}{separator}key={GOOGLE_API_KEY}"
    resp = requests.get(url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(dest_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    size = os.path.getsize(dest_path)
    logger.info(f"Download OK: {size} bytes")
    return size

@app.route('/health', methods=['GET'])
def health():
    result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
    return jsonify({'status': 'ok', 'ffmpeg': result.stdout.split('\n')[0] if result.returncode == 0 else 'not found'})

@app.route('/render', methods=['POST'])
def render():
    data = request.json
    if not data:
        return jsonify({'error': 'Body JSON obrigatório'}), 400
    required = ['roteiro_id', 'clip1_url', 'clip2_url', 'filename']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Campos obrigatórios ausentes: {missing}'}), 400

    # Sanitiza texto para uso no FFmpeg drawtext
    def sanitize(text):
        return (text or '')[:60].replace("'", " ").replace('"', ' ').replace(':', ' ').replace('\\', ' ').replace('%', 'pct').replace('\n', ' ')

    hook_text = sanitize(data.get('hook', ''))
    cta_text = sanitize(data.get('cta', ''))
    font = '/usr/share/fonts/ttf-dejavu/DejaVuSans-Bold.ttf'

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
                f"drawtext=fontfile={font}:text='{hook_text}':fontsize=42:fontcolor=white:"
                f"shadowcolor=black:shadowx=2:shadowy=2:x=(w-text_w)/2:y=h*0.08:"
                f"enable='between(t\\,0\\,4)'[v1];"
                f"[v1]drawtext=fontfile={font}:text='{cta_text}':fontsize=36:fontcolor=white:"
                f"shadowcolor=black:shadowx=2:shadowy=2:x=(w-text_w)/2:y=h*0.85:"
                f"enable='gte(t\\,12)'[vfinal]"
            )

            if music_path and os.path.exists(music_path):
                ffmpeg_cmd = ['ffmpeg', '-y', '-i', clip1_path, '-i', clip2_path, '-i', music_path,
                    '-filter_complex',
                    f"[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout];"
                    f"[aout][2:a]amix=inputs=2:duration=first:weights=1 0.15[fout];"
                    f"[vout]{text_filter}",
                    '-map', '[vfinal]', '-map', '[fout]',
                    '-c:v', 'libx264', '-crf', '23', '-preset', 'fast',
                    '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', output_path]
            else:
                ffmpeg_cmd = ['ffmpeg', '-y', '-i', clip1_path, '-i', clip2_path,
                    '-filter_complex',
                    f"[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout];"
                    f"[vout]{text_filter}",
                    '-map', '[vfinal]', '-map', '[aout]',
                    '-c:v', 'libx264', '-crf', '23', '-preset', 'fast',
                    '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', output_path]

            logger.info("Executando FFmpeg...")
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.error(f"FFmpeg stderr: {result.stderr[-2000:]}")
                return jsonify({'error': 'FFmpeg falhou', 'stderr': result.stderr[-1000:]}), 500

            output_size = os.path.getsize(output_path)
            logger.info(f"FFmpeg OK: {output_size} bytes — codificando em base64...")

            # Retorna o vídeo em base64 para o n8n fazer o upload
            with open(output_path, 'rb') as f:
                video_b64 = base64.b64encode(f.read()).decode('utf-8')

            logger.info("Base64 OK — retornando para o n8n.")
            return jsonify({
                'success': True,
                'roteiro_id': data['roteiro_id'],
                'filename': data['filename'],
                'output_size_bytes': output_size,
                'video_base64': video_b64
            })

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
