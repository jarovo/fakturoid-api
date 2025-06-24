"""Module defines the main entry point for the Apify Actor.

Feel free to modify this file to suit your specific needs.

To build Apify Actors, utilize the Apify SDK toolkit, read more at the official documentation:
https://docs.apify.com/sdk/python
"""

from __future__ import annotations

# Beautiful Soup - A library for pulling data out of HTML and XML files. Read more at:
# https://www.crummy.com/software/BeautifulSoup/bs4/doc
# Apify SDK - A toolkit for building Apify Actors. Read more at:
# https://docs.apify.com/sdk/python
from apify import Actor
from bs4 import BeautifulSoup, Tag, NavigableString
from typing import cast, assert_type


# HTTPX - A library for making asynchronous HTTP requests in Python. Read more at:
# https://www.python-httpx.org/
from httpx import AsyncClient
import logging
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.prebuilt.chat_agent_executor import AgentStructuredOutput
from openapi_pydantic import OpenAPI
from .utils import log_state
from .tools import get_fakturoid_api_description_page

def to_camel_case(text: str):
    s = text.replace("-", " ").replace("_", " ")
    s = s.split()
    if len(text) == 0:
        return text
    return s[0] + ''.join(i.capitalize() for i in s[1:])


async def parse_page(client: AsyncClient, url: str):
    Actor.log.info(f'Sending a request to {url}')

    response = await client.get(url)
    # Parse the HTML content using Beautiful Soup and lxml parser.
    soup: BeautifulSoup = BeautifulSoup(response.content, 'lxml')

    api_article_div = soup.find(class_="api-article") 
    group_name = api_article_div.parent.h1.text

    attributes_heading = api_article_div.find(id="attributes")
    if not attributes_heading:
        return None

    attribs_div = attributes_heading.find_next_sibling('div')
    attribs_table = attribs_div.find("table")

    notes_table_div = attribs_div.find_next_sibling('div')

    subobjects_headings_tags = notes_table_div.find_next_sibling('h3')
    requests_headings_tags = notes_table_div.find_next_siblings('h2')

    Actor.log.info(f"Found subobjects to parse {subobjects_headings_tags}")
    Actor.log.info(f"Found requests to parse {requests_headings_tags}")

    properties, required = parse_attributes_table(attribs_table)

    # https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.0.4.md#components-object
    
    schema_dict = {
        'type': "object",
        'properties': properties
    }
    schemas = {
        f'{to_camel_case(group_name)}': schema_dict
    }

    if required:
        schema_dict['required'] = required
        
    return schemas


SIMPLE_TYPES = {
    'String': ("string", None),
    'Integer': ("integer", None),
    'Boolean': ("boolean", None),
    'DateTime': ("string", "date-time"),
    'Datetime': ("string", "date-time"),
    'Date': ("string", "date"),
    'Decimal': ("number", "decimal")
}


def dereference(object_name: str):
    Actor.log.info(f"Dereferencing {object_name}")
    # TODO
    return object_name.lstrip('#')


def parse_attributes_table(table: BeautifulSoup) -> tuple[dict, list]:
    required = []
    properties = {}

    cols = ['vis', 'attribute', 'type', 'Description']
    
    assert table.tbody, "Table should have a tbody element"
    
    for tr in table.tbody.find_all('tr'):
        row_items = [tag for tag in tr.children if tag.name]
        vis_td, attribute_td, type_td, desc_td = row_items
        prop_name = attribute_td.code.string
        readonly = False
        if vis_td.div:
            readonly = vis_td.div.attrs['title'] == "Read-only attribute"
            if vis_td.div.attrs['title'] == "Required attribute":
                required.append(prop_name)

        soup_type_strings = list(type_td.code.strings)

        prop_type = None
        items = []
        prop_format = None
        if len(soup_type_strings) == 3 and soup_type_strings == ['Array[', 'Object', ']']:
            prop_obj_type = dereference(type_td.code.a.attrs['href'])
            prop_type = 'array'
            items = {'$ref': f'#/components/schemas/{prop_obj_type}'}
        elif len(soup_type_strings) == 1: 
            resolved_type = SIMPLE_TYPES.get(soup_type_strings[0], None)
            if resolved_type == None:
                Actor.log.error(f"Unknow type {soup_type_strings[0]}")
                prop_format = soup_type_strings[0].lower()
                prop_type = 'string'
            else:
                prop_type, prop_format = resolved_type
        else:
            Actor.log.error(f"Couldn't make sense of {type_td}")

        properties[prop_name] = {
            "type": prop_type,
            "description": desc_td.get_text(),
            "readOnly": readonly
        }
        if items:
            properties[prop_name]['items'] = items
        if prop_format:
            properties[prop_name]['format'] = prop_format

        
    return properties, required
        

async def main() -> None:
    """Define a main entry point for the Apify Actor.

    This coroutine is executed using `asyncio.run()`, so it must remain an asynchronous function for proper execution.
    Asynchronous execution is required for communication with Apify platform, and it also enhances performance in
    the field of web scraping significantly.
    """


    async with Actor:
        # Charge for Actor start
        await Actor.charge('actor-start')

        # Retrieve the input object for the Actor. The structure of input is defined in input_schema.json.
        actor_input = await Actor.get_input() or {'url': 'https://www.fakturoid.cz'}
        baseurl = actor_input.get('url')
        if not baseurl:
            raise ValueError('Missing "url" attribute in input!')

        firsturl = f'{baseurl}/api/v3'

        schemas = {}

        openapi_info = {
            "title": "Web-scraped Fakturoid V3 API",
            "description": "This is web-scraped definition of Fakturoid.",
            "contact": {
                "name": "Jaroslav Henner",
                "url": "https://github.com/jarovo/fakturoid-api/",
            },
            "license": {
                "name": "Web-scraped Fakturoid API V3 © 2025 by Jaroslav Henner is licensed under CC BY-SA 4.0. To view a copy of this license, visit https://creativecommons.org/licenses/by-sa/4.0/",
                "url": "https://creativecommons.org/licenses/by-sa/4.0/",
            },
            "version": "3.0.0-draft",
        }


        openapi_components = {
            "schemas": schemas
        }

        openapi  = {
            "openapi": "3.0.4",
            "info": openapi_info,
            "servers": [ {
                "url": "https://app.fakturoid.cz/api/v3",
                "description": 'Production Fakturoid server'
            }],
            "externalDoc": {
                "description": "Published documentation",
                "url": "https://www.fakturoid.cz/api/v3",
            },
            "components": openapi_components
        }

        # Create an asynchronous HTTPX client for making HTTP requests.
        async with AsyncClient() as client:
            # Fetch the HTML content of the page, following redirects if necessary.
            Actor.log.info(f'Sending a request to {firsturl}')
            response = await client.get(firsturl, follow_redirects=True)

            # Parse the HTML content using Beautiful Soup and lxml parser.
            soup = BeautifulSoup(response.content, 'lxml')

            for li in soup.find_all('li',  class_='pb-1'):
                path = li.a['href']
                schemas = await parse_page(client=client, url=f'{baseurl}{path}')
                if schemas:
                    for cls_name, definition in schemas.items():
                        assert cls_name not in openapi_components["schemas"]
                        openapi_components["schemas"][cls_name] = definition

        await Actor.push_data(openapi)


async def main() -> None:
    """Parse the URL using LLM."""
    actor_input = await Actor.get_input()
    query = actor_input.get('query')
    model_name = actor_input.get('modelName', 'gpt-4o-mini')
    if actor_input.get('debug', False):
        Actor.log.setLevel(logging.DEBUG)
    if not query:
        msg = 'Missing "query" attribute in input!'
        raise ValueError(msg)

    llm = ChatOpenAI(model=model_name)

    # Create the ReAct agent graph
    # see https://langchain-ai.github.io/langgraph/reference/prebuilt/?h=react#langgraph.prebuilt.chat_agent_executor.create_react_agent
    tools = [get_fakturoid_api_description_page]
    graph = create_react_agent(llm, tools, response_format=OpenAPI)

    inputs: dict = {'messages': [('user', query)]}
    response: OpenAPI | None = None
    last_message: str | None = None
    async for state in graph.astream(inputs, stream_mode='values'):
        log_state(state)
        if 'structured_response' in state:
            response = state['structured_response']
            last_message = state['messages'][-1].content
            break

    if not response or not last_message:
        Actor.log.error('Failed to get a response from the ReAct agent!')
        await Actor.fail(status_message='Failed to get a response from the ReAct agent!')
        return

    # Charge for task completion
    await Actor.charge('task-completed')

    # Push results to the key-value store and dataset
    store = await Actor.open_key_value_store()
    await store.set_value('response.txt', last_message)
    Actor.log.info('Saved the "response.txt" file into the key-value store!')

    await Actor.push_data(
        {
            'response': last_message,
            'structured_response': response.dict() if response else {},
        }
    )
    Actor.log.info('Pushed the into the dataset!')
