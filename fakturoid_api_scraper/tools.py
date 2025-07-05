from httpx import AsyncClient


async def get_fakturoid_api_description_page(page: str) -> str:
    """
    Returns the description for the Fakturoid API.
    """
    async with AsyncClient() as client:
        response = await client.get(f"https://www.fakturoid.cz/api/v3{page}")
        response.raise_for_status()
        return response.text