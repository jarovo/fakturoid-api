import pytest
from fakturoid_api_scraper import main
from json import load
from typing import Any, Dict

@pytest.fixture(scope="module")
def vcr_config():
    return {
        "decode_compressed_response": True
    }

@pytest.mark.vcr
@pytest.fixture(scope="module")
def scraped_data():
    main.main_cli()
    with open("storage/datasets/default/000000001.json", "r") as f:
        data = load(f)
        return data


def test_scrape_users(scraped_data: Any):
    current_user_get_op = scraped_data['paths']['/user.json']['get']

    assert current_user_get_op['responses']['200']['content']['application/json']['schema']['$ref'] == '#/components/schemas/Users'
    assert current_user_get_op['tags'] == ['Users']
    assert current_user_get_op['summary'] == 'Current User'

def test_scrape_invoices(scraped_data: Any):
    invoices_get_op = scraped_data['paths']['/accounts/{slug}/invoices.json']['get']

    assert invoices_get_op['responses']['200']['content']['application/json']['schema']['$ref'] == '#/components/schemas/Invoices'
    assert invoices_get_op['tags'] == ['Invoices']
    assert invoices_get_op['summary'] == 'Invoices Index'

    invoice_post_op = scraped_data['paths']['/accounts/{slug}/invoices.json']['post']
    assert invoice_post_op['responses']['201']['content']['application/json']['schema']['$ref'] == '#/components/schemas/Invoice'
    assert invoice_post_op['tags'] == ['Invoices']
    assert invoice_post_op['summary'] == 'Create Invoice'