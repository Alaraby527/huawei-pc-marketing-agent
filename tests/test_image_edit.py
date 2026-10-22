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
    assert '产品以参考图为准' in prompt
    assert '不要乱码' in prompt


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
    monkeypatch.delenv('IMAGE_API_KEY', raising=False)
    token = store_asset(PNG, 'image/png')
    _session_with_image('img-session-nokey', token)
    response = client.post('/api/v1/images/generate', json={'session_id': 'img-session-nokey'})
    assert response.status_code == 503
    assert response.json()['code'] == 'CAPABILITY_DISABLED'
    service.sessions.pop('img-session-nokey', None)


def test_scene_pipeline_composites_product_on_generated_background(monkeypatch):
    from PIL import Image
    import io as _io

    background = _io.BytesIO()
    Image.new("RGB", (1024, 1024), (200, 210, 220)).save(background, format="PNG")
    monkeypatch.setattr("app.model.generate_cover_image",
                        lambda prompt: (background.getvalue(), "image/png"))
    product = _io.BytesIO()
    Image.new("RGBA", (400, 300), (255, 255, 255, 255)).save(product, format="PNG")
    outputs = image_edit.generate_scene_image(product.getvalue(), "campus", "校园教室")
    assert outputs and outputs[0].startswith(b"\x89PNG")
    with Image.open(_io.BytesIO(outputs[0])) as image:
        assert image.size == (1080, 1440)


def test_styles_endpoint_lists_rich_variable_styles():
    data = client.get('/api/v1/images/styles').json()
    ids = {style['id'] for style in data['styles']}
    assert {'digital_handbook', 'clean_desk', 'light_comic'} <= ids
    assert len(ids) >= 10
    assert {source['id'] for source in data['sources']} == {'ai', 'kb'}


def test_kb_source_returns_original_images_without_generation(monkeypatch):
    monkeypatch.setenv('IMAGE_API_KEY', 'test-image-key')
    token = store_asset(PNG, 'image/png')
    _session_with_image('img-session-kb', token)
    called = {'n': 0}
    monkeypatch.setattr('app.image_channel.generate_from_product',
                        lambda *a, **k: called.__setitem__('n', called['n'] + 1) or [PNG])
    data = client.post('/api/v1/images/generate',
                       json={'session_id': 'img-session-kb', 'source': 'kb'}).json()
    assert data['source'] == 'kb' and data['model'] is None
    assert data['images'] == [token]
    assert called['n'] == 0
    service.sessions.pop('img-session-kb', None)


def test_ai_source_uses_dedicated_image_channel(monkeypatch):
    monkeypatch.setenv('IMAGE_API_KEY', 'test-image-key')
    token = store_asset(PNG, 'image/png')
    _session_with_image('img-session-ai', token)
    seen = {}

    def fake_generate(product_bytes, prompt, mime='image/png', model=None, size=None, n=1, ratio=None):
        seen['prompt'] = prompt
        seen['model'] = model
        return [PNG], 'primary'

    monkeypatch.setattr('app.image_channel.generate_from_product', fake_generate)
    monkeypatch.setattr('app.image_edit.has_alpha', lambda data: False)
    data = client.post('/api/v1/images/generate',
                       json={'session_id': 'img-session-ai', 'source': 'ai', 'style': 'light_comic'}).json()
    assert data['channel'] == 'image_api' and data['mode'] == 'img2img'
    assert data['images'] and data['images'][0].startswith('/api/v1/assets/')
    assert '轻漫画' in seen['prompt']
    service.sessions.pop('img-session-ai', None)


def test_ratio_follows_reference_and_crops_exactly():
    import io as _io
    from PIL import Image
    from app import image_channel

    buffer = _io.BytesIO()
    Image.new("RGB", (817, 892), (240, 240, 240)).save(buffer, format="PNG")
    ratio = image_channel.reference_ratio(buffer.getvalue())
    assert ratio and abs(ratio - 817 / 892) < 0.001
    size = image_channel.size_for_ratio(ratio)
    width, height = (int(part) for part in size.split("x"))
    assert abs(width / height - ratio) < 0.35  # snaps to the closest supported size

    wide = _io.BytesIO()
    Image.new("RGB", (1024, 1024), (255, 255, 255)).save(wide, format="PNG")
    cropped = image_channel.crop_to_ratio(wide.getvalue(), 3 / 4)
    with Image.open(_io.BytesIO(cropped)) as image:
        assert abs(image.width / image.height - 3 / 4) < 0.005


def test_ratio_preset_3_4_is_available():
    from app import image_channel
    assert image_channel.RATIO_PRESETS["3:4"] == 3 / 4
    assert image_channel.RATIO_PRESETS["kb"] is None




def test_generate_images_only_supports_image_to_image(monkeypatch):
    """只保留图生图（产品原图 + 提示词）。

    合成（背景+叠原图）、scene、edit 这些模式已删除；留着等于又开一条不走向同结果的生成
    路径，而本项目已经在"两条路径不一致"上吃过亏（热点榜单刷新对、回来就错）。
    """
    monkeypatch.setenv('IMAGE_API_KEY', 'test-image-key')
    token = store_asset(PNG, 'image/png')
    _session_with_image('img-session-i2i', token)
    calls = {}

    def fake_from_product(image_bytes, prompt, mime='image/png', model=None, size=None,
                          n=1, ratio=None, **kwargs):
        calls['prompt'] = prompt
        calls['image_bytes'] = image_bytes
        return [PNG], 'primary'

    monkeypatch.setattr('app.image_channel.generate_from_product', fake_from_product)
    monkeypatch.setattr('app.image_channel.configured', lambda: True)
    response = client.post('/api/v1/images/generate',
                           json={'session_id': 'img-session-i2i', 'style': 'campus', 'n': 1})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['mode'] == 'img2img'
    assert data['channel'] == 'image_api'
    assert data['images'], '图生图必须产出图片'
    assert calls['image_bytes'] == PNG, '必须把产品原图送进去'
    assert calls['prompt'], '必须带提示词'
    service.sessions.pop('img-session-i2i', None)


def test_the_removed_modes_are_gone_from_the_endpoint():
    """composite / scene / edit 与 dashscope 回退都不应再存在于该接口。"""
    import inspect
    from app import main
    source = inspect.getsource(main.generate_images)
    for gone in ('generate_background', '_composite', 'image_edit.edit_image',
                 'image_edit.generate_scene_image', 'dashscope'):
        assert gone not in source, gone
    assert 'generate_from_product' in source
