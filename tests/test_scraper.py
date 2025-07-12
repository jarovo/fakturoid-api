import pytest
from fakturoid_api_scraper import main
from json import load


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "decode_compressed_response": True
    }


@pytest.mark.vcr
def test_scrape():
    main.main_cli()
    data = None
    with open("storage/datasets/default/000000001.json", "r") as f:
        data = load(f)
    current_user_get_op = data['paths']['/user.json']['get']
    
    assert current_user_get_op['responses']['200']['content']['application/json']['schema']['$ref'] == '#/components/schemas/Users'
    assert current_user_get_op['tags'] == ['Users']
    assert current_user_get_op['summary'] == 'Current User'