import base64

from fastapi.testclient import TestClient

from app import image_edit
from app.conversation import Session, Slots, service
from app.main import app
from app.retrieval import store_asset

client = TestClient(app)
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==')


def test_prompt_keeps_product_appearance_and_style():
    prompt = image_edit.build_prompt(scene='课堂学习', audience='大学新生', title='开学季选本',
                                     facts=['屏幕', '电池'], style='campus')
    assert '校园' in prompt
    assert '保留产品原有外观' in prompt
    assert '不要生成文字' in prompt


def _session_with_image(sid: str, token: str):
    session = Session(sid=sid, kb_name='test')
    session.slots = Slots(audience='大学新生', scenario='课堂学习', objective='到店体验')
    session.handoff_cache = {
        'images': [{'image_id': 'I-1', 'access_url': token, 'caption': '主视觉'}],
        'facts': [{'text': '屏幕：14.2 英寸'}],
        'materials': {},
    }
    service.sessions[sid] = session
    return session


def test_generate_images_uses_product_photo_and_prompt(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'test-key')
    token = store_asset(PNG, 'image/png')
    _session_with_image('img-session-1', token)
    calls = {}

    def fake_edit(image_bytes, prompt, mime='image/png', size=None, n=1, model=None, **kwargs):
        calls['prompt'] = prompt
        calls['image_bytes'] = image_bytes
        return [PNG]

    monkeypatch.setattr('app.image_edit.edit_image', fake_edit)
    response = client.post('/api/v1/images/generate',
                           json={'session_id': 'img-session-1', 'style': 'campus', 'n': 1})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['images'] and data['images'][0].startswith('/api/v1/assets/')
    assert '校园' in data['prompt']
    assert calls['image_bytes'] == PNG
    assert '氛围背景' in data['note']
    service.sessions.pop('img-session-1', None)


def test_generate_images_requires_reference(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'test-key')
    response = client.post('/api/v1/images/generate', json={'session_id': 'missing-session'})
    assert response.status_code == 422
    assert response.json()['code'] == 'EVIDENCE_REQUIRED'


def test_generate_images_reports_expired_reference(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'test-key')
    _session_with_image('img-session-2', '/api/v1/assets/expired-token')
    response = client.post('/api/v1/images/generate', json={'session_id': 'img-session-2'})
    assert response.status_code == 409
    assert response.json()['code'] == 'SOURCE_UNAVAILABLE'
    service.sessions.pop('img-session-2', None)


def test_generate_images_without_key_is_disabled(monkeypatch):
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    response = client.post('/api/v1/images/generate', json={})
    assert response.status_code == 503
    assert response.json()['code'] == 'CAPABILITY_DISABLED'
