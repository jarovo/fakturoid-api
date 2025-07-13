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
from typing import cast, assert_type, Dict, Tuple, Iterator, Self, List


# HTTPX - A library for making asynchronous HTTP requests in Python. Read more at:
# https://www.python-httpx.org/
from httpx import AsyncClient
import logging
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from openapi_pydantic import OpenAPI, Info, Server, ExternalDocumentation, PathItem
import openapi_pydantic.v3.v3_1 as opy
from fakturoid_api_scraper.utils import log_state
from fakturoid_api_scraper.tools import get_fakturoid_api_description_page
from dataclasses import dataclass


API_BASEURL = "https://app.fakturoid.cz/api/v3"


class ParseError(Exception):
    pass


def to_camel_case(text: str):
    s = text.replace("-", " ").replace("_", " ")
    s = s.split()
    if len(text) == 0:
        return text
    return s[0] + ''.join(i.capitalize() for i in s[1:])


def parse_table(table: Tag) -> Dict[str, opy.Schema]:
    params: Dict[str, opy.Schema] = {}
    rows = table.find_all('tr')
    for row in rows:
        cols = row.find_all('td')
        if len(cols) >= 2:
            param_name = cols[0].text.strip()
            param_description = cols[1].text.strip()
            params[param_name] = param_description
            Actor.log.info(f"Found parameter {param_name} with description: {param_description}")
    return params


def parse_path_item(button_tag: Tag) -> Tuple[str, str] | None:
    """Parses a button tag to extract the HTTP path and operations."""

    operation: str
    path: str

    if method_tag := button_tag.find('code'):
        operation = method_tag.text.strip().lower()
        if operation not in ('get', 'post', 'patch', 'delete'):
            raise ParseError(f"Unexpected method {operation} in {button_tag}")
        path = method_tag.find_next_sibling('code').text.strip()
        Actor.log.info(f"Found requests: {operation} {path}")
        return operation, path
    elif method_tag := button_tag.find(string="Payload example"):
        raise ParseError(f"Webhook-delivery context found in {button_tag}")
    else:
        raise ParseError(f"Method tag not found in {button_tag}")


class TableRow:
    def __init__(self, tag: Tag):
        assert tag.name == 'tr', "TableRow should be initialized with a <tr> tag"
        self.tag = tag

    def __iter__(self) -> Iterator[Tag]:
        for tag in self.tag.contents:
            if isinstance(tag, Tag):
                assert tag.name in ('td', 'th'), "TableRow should only contain <td> or <th> tags"
                yield tag.text.strip()

class Table:
    def __init__(self, tag: Tag):
        assert tag.name == 'table', "Table should be initialized with a <table> tag"
        self.tag = tag

    def thead_items(self):
        thead_tag: Tag = self.tag.find('thead')
        thead_tr_tag: Tag = thead_tag.find('tr')

        for th in thead_tr_tag.find_all('th'):
            yield th.text.strip()

    def __iter__(self) -> Iterator[TableRow]:
        tbody_tag: Tag = self.tag.find('tbody')
        yield from (TableRow(row) for row in tbody_tag.find_all('tr'))



PARAMETERS_IN_TABLE = {
    'URL Parameters': 'path',
    'Query Parameters': 'query',
}


def parse_request_description_div_tag(request_description_div_tag: Tag, openapi: OpenAPI) -> None:
    heading: str
    table: Tag
    parameters: List[opy.Parameter] = []

    for h4_tag in request_description_div_tag.find_all('h4'):    
        heading = h4_tag.text.strip()

        param_in = PARAMETERS_IN_TABLE.get(heading, None)
        if not param_in:
            Actor.log.warning(f"Unknown heading {heading} in request description div tag.")
            continue

        Actor.log.info(f"Found heading: {heading}")

        div = h4_tag.find_next('div')
        table = div.find('table')
        Actor.log.info(f"Found {heading} table")
        table = Table(table)
        for t_row in table:
            try:
                name, description, type_, example = t_row
            except ValueError as e:
                Actor.log.error(f"Failed to parse table row {t_row.tag}: {e}")
                continue

            parameters.append(opy.Parameter(
                name=name,
                param_in=param_in,
                description=description,
                schema=opy.Schema(
                    type=type_.lower(),
                    example=example
                )
            ))
        return parameters

@dataclass
class Path:
    summary: str
    description: str
    operation: str
    path: str
    parameters: List[opy.Parameter]

    @classmethod
    def parse(cls, title_tag: Tag, openapi: OpenAPI) -> Self:
        title: str = title_tag.text.strip()
        description: str
        operation: str
        path: str

        Actor.log.info(f"Parsing request {title}")

        if description_tag := title_tag.find_next('ul'):
            description = description_tag.text.strip()
        else:
            Actor.log.warning(f"Description not found for request {title}")
            description = "No description provided."
            
        toggle_content_tag = title_tag.find_next('div', attrs={'data-controller': 'toggle-content'})
        assert isinstance(toggle_content_tag, Tag), "Toggle content tag should be a Tag instance"

        request_button = toggle_content_tag.find('button')
        assert isinstance(request_button, Tag), "Request button should be a Tag instance"
        operation, path = parse_path_item(request_button)

        request_description_div_tag = toggle_content_tag.find('div', attrs={'data-toggle-content-target': 'content'})
        assert isinstance(request_description_div_tag, Tag), "Request description div tag should be a Tag instance"

        parameters = parse_request_description_div_tag(request_description_div_tag, openapi)

        return cls(summary=title, description=description, operation=operation, path=path, parameters=parameters)

    def fill_openapi(self, openapi: OpenAPI, tag_name:str, entity_name: str) -> None:
        """Fills the OpenAPI object with the method data."""
        Actor.log.info(f"Filling OpenAPI with {self.operation} {self.path} for {tag_name} and {entity_name} entity.")

        if path_item := openapi.paths.get(self.path, opy.PathItem()):

            if operation := getattr(path_item, self.operation.lower(), None):
                raise ValueError(f"Operation {self.operation} {self.path} already exists.")

            operation = opy.Operation(
                summary=self.summary,
                description=self.description,
                tags=[entity_name],
                parameters=self.parameters,
                responses={
                    "200": opy.Response(
                        description="Successful response",
                        content={
                            "application/json": opy.MediaType(
                                schema=opy.Reference(ref=f'#/components/schemas/{entity_name}')
                            )
                        }
                    )
                },
            )
            setattr(path_item, self.operation.lower(), operation)

        openapi.paths[self.path] = path_item


async def parse_article(page: BeautifulSoup, article_tag: Tag, openapi: OpenAPI) -> None:
    """Parses an article tag to extract the API path and operations."""
    Actor.log.info(f"Parsing article {article_tag}")
    title = article_tag.parent.h1.text.strip()

    attributes_heading = article_tag.find(id="attributes")
    if not attributes_heading:
        return None

    attribs_div = attributes_heading.find_next_sibling('div')
    attribs_table = attribs_div.find("table")

    notes_table_div = attribs_div.find_next_sibling('div')

    subobjects_headings_tags = notes_table_div.find_next_sibling('h3')
    requests_headings_tags = notes_table_div.find_next_siblings('h2')

    Actor.log.info(f"Found subobjects to parse {subobjects_headings_tags}")
    Actor.log.info(f"Found requests to parse {requests_headings_tags}")

    parse_attributes_table(page, attribs_table, title, openapi)
    # https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.0.4.md#components-object
    for heading_tag in requests_headings_tags:
        assert isinstance(heading_tag, Tag), "Heading tag should be a Tag instance"
        try:
            method = Path.parse(heading_tag, openapi)
            method.fill_openapi(openapi, heading_tag.text.strip(), title)
        except ParseError as e:
            Actor.log.error(f"Failed to parse {heading_tag}: {e}")


async def parse_page(client: AsyncClient, url: str, openapi: OpenAPI):
    Actor.log.info(f'Sending a request to {url}')

    response = await client.get(url)
    # Parse the HTML content using Beautiful Soup and lxml parser.
    soup: BeautifulSoup = BeautifulSoup(response.content, 'lxml')

    api_article_div = soup.find(class_="api-article") 
    await parse_article(soup, api_article_div, openapi)

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


def parse_attributes_table(page: BeautifulSoup, table: BeautifulSoup, group_name: str, openapi: opy.OpenAPI) -> None:
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
            if type_td.code.a['href'] == '#eet-records':
                Actor.log.info("Skipping eet records, they are not supported by Fakturoid API.")
                items = opy.Reference(ref='#/components/schemas/EET Records')
            elif type_td.code.a['href'] == '/api/v3/invoice-payments':
                Actor.log.info("Skipping invoice payments, they are not supported by Fakturoid API.")
                items = opy.Reference(ref='#/components/schemas/Invoice Payments')
            elif type_td.code.a['href'] == '/api/v3/expense-payments':
                Actor.log.info("Skipping expense payments, they are not supported by Fakturoid API.")
                items = opy.Reference(ref='#/components/schemas/Expense Payments')
            elif type_td.code.a['href'] == '#attachments':
                Actor.log.info("Skipping attachments, they are not supported by Fakturoid API.")
                items = opy.Reference(ref='#/components/schemas/Attachments')
            else:
                table = find_subattrs_table(page, type_td.code.a['href'])
                title = table.find_previous('h2').text.strip()
                parse_attributes_table(page, table, title, openapi)
                prop_type = opy.DataType.ARRAY
                items = opy.Reference(ref=f'#/components/schemas/{title}')
        elif len(soup_type_strings) == 1:
            resolved_type = SIMPLE_TYPES.get(soup_type_strings[0], None)
            if resolved_type == None:
                Actor.log.error(f"Unknown type {soup_type_strings[0]}")
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


def find_subattrs_table(page: BeautifulSoup, href: str) -> Tag | None:
    Actor.log.info(f"Finding subattributes table for {href}")
    retval = page.find(id=href.lstrip('#')).find_next('table')
    assert retval
    return retval


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
            schemas=schemas,
            securitySchemes={
                'OAuth2': opy.SecurityScheme(type='oauth2',
                                             description='OAuth2 security scheme for Fakturoid API',
                                             flows=opy.OAuthFlows(
                                                 clientCredentials=opy.OAuthFlow(tokenUrl=f'{API_BASEURL}/oauth/token', scopes={})))
            }
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
            security=[dict(OAuth2=[])],
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