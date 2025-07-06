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
from bs4 import BeautifulSoup, Tag, NavigableString, ResultSet
from typing import cast, assert_type, Dict, Tuple, Iterator, Self


# HTTPX - A library for making asynchronous HTTP requests in Python. Read more at:
# https://www.python-httpx.org/
from httpx import AsyncClient
import logging
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from openapi_pydantic import OpenAPI, Info, Server, ExternalDocumentation, PathItem
import openapi_pydantic as opy
from fakturoid_api_scraper.utils import log_state
from fakturoid_api_scraper.tools import get_fakturoid_api_description_page
from dataclasses import dataclass


def to_camel_case(text: str):
    s = text.replace("-", " ").replace("_", " ")
    s = s.split()
    if len(text) == 0:
        return text
    return s[0] + ''.join(i.capitalize() for i in s[1:])



@dataclass
class Method:
    title: str
    description: str
    operation: str
    path: str

    @classmethod
    def parse(cls, title_tag: Tag, openapi: OpenAPI) -> Iterator[Self]:
        """Returns the next sibling of a tag that is not a NavigableString."""
        title = title_tag.text.strip()
        description = None
        operation = None
        path = None

        Actor.log.info(f"Parsing request {title}")
        for tag in title_tag.find_next_siblings():
            print(tag)
            if isinstance(tag, NavigableString):
                continue
            elif isinstance(tag, Tag):
                if tag.name in ('ul', 'p') or 'table-scroll' in tag.get('class', []):
                    Actor.log.info(f"Found request description: {tag.text.strip()}")
                    description = tag.text.strip()
                elif tag.name == 'div':
                    Actor.log.info(f"Found request div: {tag}")
                    request_button = tag.find('button')
                    assert request_button, "Request button should be present in the div"
                    if method_tag := request_button.find('code'):
                        operation = method_tag.text.strip().lower()
                        if operation not in ('get', 'post', 'patch', 'delete'):
                            Actor.log.warning(f"Unexpected method {operation} in request {title}")
                            continue
                        path = method_tag.find_next_sibling('code').text.strip()
                        Actor.log.info(f"Found requests: {title} {operation} {path}")
                    elif example_lead := request_button.find('span', text='Payload example'):
                        if example_tag := example_lead.find_next('div', attrs={'data-toggle-content-target': 'content'}):
                            description += f"\nPayload example:" + str(example_tag)
                            continue
                        else:
                            Actor.log.warning(f"Payload example not found in request {title}")
                elif tag.name == 'h3' and tag.text.strip() == 'Request':
                    Actor.log.info(f"Found request title: {title}")
                    # Continue to the next tag, which should be the description or div with method and path
                    continue
                elif tag.name == "h3" and tag.text.strip() == "Response":
                    Actor.log.info(f"Found response for request {title} {operation} {path}")
                    # We are done with the request, yield it
                    if title and description and operation and path:
                        yield cls(title=title, description=description, operation=operation, path=path)
                    break
                elif tag.name == "h3" and tag.text.strip() == "Payload Content":
                    Actor.log.info(f"Found payload content for request {title} {operation} {path}")
                    description += tag.text.strip() +str(tag.next_sibling)
                elif tag.name == "h2":
                    Actor.log.warning(f"Found h2 tag {tag} in request {title}")
                    if title and description and operation and path:
                        yield cls(title=title, description=description, operation=operation, path=path)
                    break
                else:
                    Actor.log.warning(f"Unexpected tag {tag} in request {title}")
                    break
        else:
            Actor.log.warning(f"Request {title} not fully parsed")

    def fill_openapi(self, openapi: OpenAPI, group_name: str) -> None:
        """Fills the OpenAPI object with the method data."""
        Actor.log.info(f"Filling OpenAPI with {self.operation} {self.path} for group {group_name}")

        if path_item := openapi.paths.get(self.path, opy.PathItem()):
            if operation := getattr(path_item, self.operation.lower(), None):
                raise ValueError(f"Operation {self.operation} {self.path} already exists.")

            operation = opy.Operation(
                summary=self.title,
                description=self.description,
                tags=[group_name],
                responses={
                    "200": opy.Response(
                        description="Successful response",
                        content={
                            "application/json": opy.MediaType(
                                schema=opy.Reference(ref=f'#/components/schemas/{group_name}')
                            )
                        }
                    )
                },
            )
            setattr(path_item, self.operation.lower(), operation)

        openapi.paths[self.path] = path_item

async def parse_page(client: AsyncClient, url: str, openapi: OpenAPI):
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

    parse_attributes_table(attribs_table, group_name, openapi)
    # https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.0.4.md#components-object
    for heading_tag in requests_headings_tags:
        assert isinstance(heading_tag, Tag), "Heading tag should be a Tag instance"
        method = [m for m in Method.parse(heading_tag, openapi) if m is not None]
        for m in method:
            m.fill_openapi(openapi, group_name)

SIMPLE_TYPES: Dict[str, Tuple[opy.DataType, str|None]] = {
    'String': (opy.DataType.STRING, None),
    'Integer': (opy.DataType.INTEGER, None),
    'Boolean': (opy.DataType.BOOLEAN, None),
    'DateTime': (opy.DataType.STRING, "date-time"),
    'Datetime': (opy.DataType.STRING, "date-time"),
    'Date': (opy.DataType.STRING, "date"),
    'Decimal': (opy.DataType.NUMBER, "decimal")
}


def dereference(object_name: str):
    Actor.log.info(f"Dereferencing {object_name}")
    # TODO
    return object_name.lstrip('#')


def parse_attributes_table(table: BeautifulSoup, group_name: str, openapi: opy.OpenAPI) -> None:
    schema = opy.Schema(
        type=opy.DataType.OBJECT,
        description="This schema is web-scraped from Fakturoid API documentation.",
        properties={},
        required=[],
    )

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
                assert schema.required is not None, "Schema should have a required list"
                schema.required.append(prop_name)

        soup_type_strings = list(type_td.code.strings)

        prop_type = None
        items = None
        prop_format = None
        if len(soup_type_strings) == 3 and soup_type_strings == ['Array[', 'Object', ']']:
            prop_obj_type = dereference(type_td.code.a.attrs['href'])
            prop_type = opy.DataType.ARRAY
            items = opy.Reference(ref=f'#/components/schemas/{prop_obj_type}')
        elif len(soup_type_strings) == 1:
            resolved_type = SIMPLE_TYPES.get(soup_type_strings[0], None)
            if resolved_type == None:
                Actor.log.error(f"Unknow type {soup_type_strings[0]}")
                prop_format = soup_type_strings[0].lower()
                prop_type = opy.DataType.STRING
            else:
                prop_type, prop_format = resolved_type
        else:
            Actor.log.error(f"Couldn't make sense of {type_td}")

        assert schema.properties is not None, "Schema should have a properties dict"
        schema.properties[prop_name] = opy.Schema(
            type=prop_type,
            description=desc_td.get_text(),
            readOnly=readonly,
        )
        if items:
            schema.properties[prop_name].items = items
        if prop_format:
            schema.properties[prop_name].schema_format = prop_format
        
    openapi.components.schemas[group_name] = schema

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

        schemas: Dict[str, opy.Schema] = {}

        openapi_info = opy.Info(
            title="Web-scraped Fakturoid V3 API",
            description="This is web-scraped definition of Fakturoid.",
            contact=opy.Contact(
                name="Jaroslav Henner",
                url="https://github.com/jarovo/fakturoid-api/",
            ),
            license=opy.License(
                name="Web-scraped Fakturoid API V3 © 2025 by Jaroslav Henner is licensed under CC BY-SA 4.0. To view a copy of this license, visit https://creativecommons.org/licenses/by-sa/4.0/",
                url="https://creativecommons.org/licenses/by-sa/4.0/",
            ),
            version="3.0.0-draft",
        )


        openapi_components = opy.Components(
            schemas=schemas
        )

        openapi  = opy.OpenAPI(
            info=openapi_info,
            servers=[opy.Server(
                url="https://app.fakturoid.cz/api/v3",
                description='Production Fakturoid server'
            )],
            externalDocs=opy.ExternalDocumentation(
                description="Published documentation",
                url="https://www.fakturoid.cz/api/v3"
            ),
            components=openapi_components,
            paths={},
        )

        # Create an asynchronous HTTPX client for making HTTP requests.
        async with AsyncClient() as client:
            # Fetch the HTML content of the page, following redirects if necessary.
            Actor.log.info(f'Sending a request to {firsturl}')
            response = await client.get(firsturl, follow_redirects=True)

            # Parse the HTML content using Beautiful Soup and lxml parser.
            soup = BeautifulSoup(response.content, 'lxml')

            for li in soup.find_all('li',  class_='pb-1'):
                path = li.a['href']
                await parse_page(client=client, url=f'{baseurl}{path}', openapi=openapi)

        await Actor.push_data(openapi.model_dump_json(indent=2, exclude_none=True))


def main_cli() -> None:
    """Define a main entry point for the Apify Actor.

    This function is executed when the script is run directly, e.g. `python -m src.fakturoid_api_scraper`.
    """
    import asyncio
    asyncio.run(main())


if __name__ == '__main__':
    # If the script is run directly, execute the main function.
    main_cli()