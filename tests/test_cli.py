"""CLI handoff export must be idempotent across turns."""
import tempfile
from pathlib import Path
from app import retrieval
from scripts.chat import export_result

PNG = b'\x89PNG\r\n\x1a\ncli-test'


def test_export_twice_keeps_handoff_valid():
    retrieval.ASSETS.clear()
    url = retrieval.store_asset(PNG, 'image/png')
    result = {'session_id': 's', 'can_generate': True, 'handoff': {
        'images': [{'image_id': 'I-1', 'access_url': url}],
        'materials': {'images': [{'image_id': 'I-1', 'access_url': url}]},
        'data_state': {'status': 'ready', 'can_generate': True, 'reasons': []}}}
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / 'handoff.json'
        export_result(result, output)
        first = Path(result['handoff']['images'][0]['access_url'])
        assert first.is_file() and first.read_bytes() == PNG
        export_result(result, output)
        assert result['can_generate'] is True
        assert result['handoff']['data_state']['status'] == 'ready'
        assert result['handoff']['images'][0]['access_url'] == str(first)
