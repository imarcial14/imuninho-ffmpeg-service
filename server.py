#!/usr/bin/env python3
"""
FFmpeg Service para Imuninho
Recebe clips via URL do Google Drive, monta o vídeo e faz upload de volta.
"""
from flask import Flask, request, jsonify
import subprocess
import os
import tempfile
import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY', '')
DRIVE_FOLDER_ID = os.environ.get('DRIVE_FOLDER_ID', '')

def download_file(url, dest_path):
    """Baixa arquivo de URL (Google Drive ou GCS) para dest_path."""
    logger.info(f"Baixando: {url[:80]}...")
    
    # Se for URL do Google Cloud Storage (gs://)
    if url.startswith('gs://'):
        # Converte para URL pública HTTPS
        url = url.replace('gs://', 'https://storage.googleapis.com/')
    
    headers = {}
    if 'googleapis.com' in url and GOOGLE_API_KEY:
        headers['Authorization'] = f'Bearer {GOOGLE_API_KEY}'
    
    resp = requests.get(url, headers=headers, stream=True, timeout=120)
    resp.raise_for_status()
    
    with open(dest_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    
    size = os.path.getsize(dest_path)
    logger.info(f"Download OK: {size} bytes")
    return size

def upload_to_drive(file_path, filename, folder_id, access_token):
    """Faz upload do arquivo para o Google Drive via API."""
    logger.info(f"Uploading {filename} para Drive folder {folder_id}...")
    
    metadata = {
        'name': filename,
        'parents': [folder_id]
    }
    
    with open(file_path, 'rb') as f:
        files = {
            'data': ('metadata', str(metadata).replace("'", '"'), 'application/json; charset=UTF-8'),
            'file': (filename, f, 'video/mp4')
        }
        
        resp = requests.post(
            'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,webViewLink',
            headers={'Authorization': f'Bearer {access_token}'},
            files=files,
            timeout=300
        )
    
    resp.raise_for_status()
    result = resp.json()
    logger.info(f"Upload OK: {result.get('id')}")
    return result

@app.route('/health', methods=['GET'])
def health():
    """Health check."""
    result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
    return jsonify({
        'status': 'ok',
        'ffmpeg': result.stdout.split('\n')[0] if result.returncode == 0 else 'not found'
    })

@app.route('/render', methods=['POST'])
def render():
    """
    Renderiza vídeo a partir de clips + trilha.
    
    Body JSON:
    {
        "roteiro_id": 1,
        "hook": "texto do hook",
        "cta": "texto do CTA",
        "clip1_url": "https://...",
        "clip2_url": "https://...",
        "music_url": "https://..." (opcional),
        "access_token": "Bearer token OAuth2",
        "folder_id": "ID da pasta do Drive",
        "filename": "imuninho_r1_20260101.mp4"
    }
    """
    data = request.json
    if not data:
        return jsonify({'error': 'Body JSON obrigatório'}), 400
    
    required = ['roteiro_id', 'clip1_url', 'clip2_url', 'access_token', 'folder_id', 'filename']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Campos obrigatórios ausentes: {missing}'}), 400
    
    hook_text = data.get('hook', '').replace("'", "\\'")[:60]
    cta_text = data.get('cta', '').replace("'", "\\'")[:60]
    
    with tempfile.TemporaryDirectory() as tmpdir:
        clip1_path = os.path.join(tmpdir, 'clip1.mp4')
        clip2_path = os.path.join(tmpdir, 'clip2.mp4')
        output_path = os.path.join(tmpdir, data['filename'])
        
        try:
            # Download dos clips
            download_file(data['clip1_url'], clip1_path)
            download_file(data['clip2_url'], clip2_path)
            
            # Trilha opcional
            music_path = None
            if data.get('music_url'):
                music_path = os.path.join(tmpdir, 'music.mp3')
                try:
                    download_file(data['music_url'], music_path)
                except Exception as e:
                    logger.warning(f"Trilha falhou, continuando sem música: {e}")
                    music_path = None
            
            # Monta comando FFmpeg
            if music_path and os.path.exists(music_path):
                # Com trilha + texto overlay
                ffmpeg_cmd = [
                    'ffmpeg', '-y',
                    '-i', clip1_path,
                    '-i', clip2_path,
                    '-i', music_path,
                    '-filter_complex',
                    f"[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout];"
                    f"[aout][2:a]amix=inputs=2:duration=first:weights=1 0.15[fout];"
                    f"[vout]drawtext=text='{hook_text}':fontsize=42:fontcolor=white:"
                    f"shadowcolor=black:shadowx=2:shadowy=2:x=(w-text_w)/2:y=h*0.08:"
                    f"enable='between(t,0,4)'[v1];"
                    f"[v1]drawtext=text='{cta_text}':fontsize=36:fontcolor=white:"
                    f"shadowcolor=black:shadowx=2:shadowy=2:x=(w-text_w)/2:y=h*0.85:"
                    f"enable='gte(t,12)'[vfinal]",
                    '-map', '[vfinal]',
                    '-map', '[fout]',
                    '-c:v', 'libx264', '-crf', '23', '-preset', 'fast',
                    '-c:a', 'aac', '-b:a', '128k',
                    '-movflags', '+faststart',
                    output_path
                ]
            else:
                # Sem trilha + texto overlay
                ffmpeg_cmd = [
                    'ffmpeg', '-y',
                    '-i', clip1_path,
                    '-i', clip2_path,
                    '-filter_complex',
                    f"[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout];"
                    f"[vout]drawtext=text='{hook_text}':fontsize=42:fontcolor=white:"
                    f"shadowcolor=black:shadowx=2:shadowy=2:x=(w-text_w)/2:y=h*0.08:"
                    f"enable='between(t,0,4)'[v1];"
                    f"[v1]drawtext=text='{cta_text}':fontsize=36:fontcolor=white:"
                    f"shadowcolor=black:shadowx=2:shadowy=2:x=(w-text_w)/2:y=h*0.85:"
                    f"enable='gte(t,12)'[vfinal]",
                    '-map', '[vfinal]',
                    '-map', '[aout]',
                    '-c:v', 'libx264', '-crf', '23', '-preset', 'fast',
                    '-c:a', 'aac', '-b:a', '128k',
                    '-movflags', '+faststart',
                    output_path
                ]
            
            logger.info("Executando FFmpeg...")
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=600)
            
            if result.returncode != 0:
                logger.error(f"FFmpeg stderr: {result.stderr[-2000:]}")
                return jsonify({
                    'error': 'FFmpeg falhou',
                    'stderr': result.stderr[-1000:]
                }), 500
            
            output_size = os.path.getsize(output_path)
            logger.info(f"FFmpeg OK: {output_size} bytes")
            
            # Upload para Drive
            drive_result = upload_to_drive(
                output_path,
                data['filename'],
                data['folder_id'],
                data['access_token']
            )
            
            return jsonify({
                'success': True,
                'roteiro_id': data['roteiro_id'],
                'filename': data['filename'],
                'file_id': drive_result.get('id'),
                'drive_link': drive_result.get('webViewLink'),
                'output_size_bytes': output_size
            })
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro de download: {e}")
            return jsonify({'error': f'Erro de download: {str(e)}'}), 500
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'FFmpeg timeout (>600s)'}), 504
        except Exception as e:
            logger.error(f"Erro inesperado: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"FFmpeg Service iniciando na porta {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
