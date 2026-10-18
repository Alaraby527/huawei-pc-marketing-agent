import copy
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
EXAMPLE = json.loads((Path(__file__).parents[1] / 'examples/materials.development.json').read_text())

def live_materials():
    source = {'kind': 'ima', 'title': '单元测试证据对象，不是真实知识库资料',
              'locator': 'test-only', 'excerpt': '此对象仅检查接口行为，不陈述任何华为产品事实。'}
    return {
        **copy.deepcopy(EXAMPLE), 'data_mode': 'live', 'status': 'ready', 'issues': [],
        'product': {'product_id': 'test-product', 'model_name': '单元测试对象', 'configuration': '测试配置', 'confirmed': True},
        'facts': [{'fact_id': 'F1', 'product_id': 'test-product', 'text': '测试事实对象', 'source': source}],
        'images': [{'image_id': f'I{i}', 'product_id': 'test-product',
                    'access_url': f'https://example.invalid/test-{i}.png', 'caption': '单元测试，不用于发布',
                    'model_verified': True, 'source': source} for i in range(3)]
    }

def test_health_does_not_claim_business_ready():
    assert client.get('/healthz').json()['status'] == 'ok'
    assert client.get('/api/v1/readiness').json()['business_ready'] is False

def test_secrets_are_not_exposed_by_readiness(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'test-secret-must-not-leak')
    monkeypatch.setenv('IMA_OPENAPI_APIKEY', 'test-secret-two')
    response = client.get('/api/v1/readiness')
    assert response.json()['configured']['llm_credentials'] is True
    assert 'test-secret' not in response.text

def test_home_and_platforms_available():
    response = client.get('/')
    assert response.status_code == 200
    assert '营销工作台' in response.text
    assert len(client.get('/api/v1/platforms').json()) == 4

def test_schema_available_for_teammate():
    schema = client.get('/api/v1/contracts/materials').json()
    assert {'schema_version', 'task', 'facts', 'images', 'status'}.issubset(schema['properties'])

def test_development_sample_cannot_pass_prechecks_or_publish():
    result = client.post('/api/v1/materials/validate', json=EXAMPLE)
    assert result.status_code == 200
    assert result.json()['prechecks_passed'] is False
    assert result.json()['can_publish'] is False

def test_structurally_valid_package_does_not_prove_truth_or_publishability():
    result = client.post('/api/v1/materials/validate', json=live_materials()).json()
    assert result['prechecks_passed'] is True
    assert result['can_publish'] is False
    assert '来源真实性' in result['reason']

@pytest.mark.parametrize('part', ['facts', 'images'])
def test_cross_product_materials_rejected(part):
    materials = live_materials()
    materials[part][0]['product_id'] = 'different-product'
    assert client.post('/api/v1/materials/validate', json=materials).status_code == 422

@pytest.mark.parametrize('part', ['facts', 'images'])
def test_development_source_not_accepted_as_live(part):
    materials = live_materials()
    materials[part][0]['source']['kind'] = 'development'
    assert client.post('/api/v1/materials/validate', json=materials).status_code == 422

@pytest.mark.parametrize('part', ['facts', 'images'])
def test_duplicate_identifiers_rejected(part):
    materials = live_materials()
    materials[part].append(copy.deepcopy(materials[part][0]))
    assert client.post('/api/v1/materials/validate', json=materials).status_code == 422

@pytest.mark.parametrize('status', ['needs_input', 'conflict', 'unavailable'])
def test_upstream_problem_blocks_prechecks(status):
    materials = live_materials()
    materials['status'] = status
    result = client.post('/api/v1/materials/validate', json=materials).json()
    assert result['prechecks_passed'] is False

def test_insufficient_verified_images_blocked():
    materials = live_materials()
    materials['images'][0]['model_verified'] = False
    result = client.post('/api/v1/materials/validate', json=materials).json()
    assert result['usable_image_count'] == 2
    assert result['prechecks_passed'] is False

@pytest.mark.parametrize('change', ['no_product', 'unconfirmed', 'no_facts'])
def test_ready_requires_confirmed_product_and_facts(change):
    materials = live_materials()
    if change == 'no_product': materials['product'] = None
    if change == 'unconfirmed': materials['product']['confirmed'] = False
    if change == 'no_facts': materials['facts'] = []
    assert client.post('/api/v1/materials/validate', json=materials).status_code == 422

def test_validation_error_does_not_echo_input():
    materials = {**EXAMPLE, 'unexpected_secret': 'test-secret-not-to-echo'}
    result = client.post('/api/v1/materials/validate', json=materials)
    assert result.status_code == 422
    assert 'test-secret-not-to-echo' not in result.text

def test_large_stream_rejected():
    result = client.post('/api/v1/materials/validate', content=b'x' * 256_001,
                         headers={'content-type': 'application/json'})
    assert result.status_code == 413

def test_task_does_not_fabricate_success(monkeypatch):
    from app.contracts import MaterialBundle
    async def no_retrieval(task):
        return MaterialBundle(request_id='test-pending',data_mode='live',status='unavailable',task=task)
    monkeypatch.setattr('app.main.retrieve_materials',no_retrieval)
    result = client.post('/api/v1/tasks', json=EXAMPLE['task'])
    assert result.status_code == 503
    assert result.json()['status'] == 'integration_pending'
    assert result.json()['can_publish'] is False

def test_api_not_cached():
    assert client.get('/api/v1/readiness').headers['cache-control'] == 'no-store'
