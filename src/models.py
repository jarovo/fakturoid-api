from pydantic import BaseModel, Field


class Contact(BaseModel):
    """
    Represents contact information for the API.
    """
    name: str = Field(default="", description="The name of the contact person or organization")
    url: str = Field(default="", description="The URL of the contact person or organization")
    email: str = Field(default="", description="The email address of the contact person or organization")


class License(BaseModel):
    """
    Represents license information for the API.
    """
    name: str = Field(..., description="The name of the license")
    url: str = Field(default="", description="The URL of the license")
    identifier: str = Field(default="", description="The identifier of the license, if applicable")


class ServerVariable(BaseModel):
    """
    Represents a variable for server URL templating.
    """
    enum: list[str] = Field(default_factory=list, description="An enumeration of string values to be used if the substitution options are from a limited set")
    default: str = Field(..., description="The default value to use for substitution")
    description: str = Field(default="", description="A description of the server variable")


class Server(BaseModel):
    """
    Represents a server where the API is hosted.
    """
    url: str = Field(..., description="The URL of the server")
    description: str = Field(default="", description="A brief description of the server")
    variables: dict[str, ServerVariable] = Field(default_factory=dict, description="Variables for server URL templating, if applicable")


class Info(BaseModel):
    """
    Represents the information about the API.
    """
    title: str = Field(..., description="The title of the API")
    summary: str = Field(default="", description="A brief summary of the API")
    description: str = Field(default="", description="A detailed description of the API")
    termsOfService: str = Field(default="", description="Terms of service for the API")
    contact: Contact = Field(default_factory=Contact, description="Contact information for the API")
    license: License = Field(default_factory=License, description="License information for the API")
    version: str = Field(..., description="The version of the API")


class Schema(BaseModel):
    """
    Represents a schema in the OpenAPI specification.
    """
    type: str = Field(..., description="The type of the schema (e.g., object, array, string)")
    properties: dict[str, 'Schema'] = Field(default_factory=dict, description="Properties of the schema if it is an object")
    items: 'Schema' = Field(default=None, description="Items of the schema if it is an array")
    required: list[str] = Field(default_factory=list, description="Required properties of the schema")
    description: str = Field(default="", description="A description of the schema")
    example: dict = Field(default_factory=dict, description="An example of the schema")


class Response(BaseModel):
    """
    Represents a response in the OpenAPI specification.
    """
    description: str = Field(..., description="A description of the response")
    content: dict[str, Schema] = Field(default_factory=dict, description="Content of the response, keyed by media type")
    headers: dict[str, Schema] = Field(default_factory=dict, description="Headers of the response")
    links: dict[str, Schema] = Field(default_factory=dict, description="Links related to the response")


class Components(BaseModel):
    """
    Represents the components of the API, such as schemas and responses.
    """
    schemas: dict[str, Schema] = Field(default_factory=dict, description="Schemas used in the API")
    responses: dict[str, Schema] = Field(default_factory=dict, description="Responses used in the API")
    parameters: dict[str, Schema] = Field(default_factory=dict, description="Parameters used in the API")
    examples: dict[str, Schema] = Field(default_factory=dict, description="Examples used in the API")
    requestBodies: dict[str, Schema] = Field(default_factory=dict, description="Request bodies used in the API")
    headers: dict[str, Schema] = Field(default_factory=dict, description="Headers used in the API")
    securitySchemes: dict[str, Schema] = Field(default_factory=dict, description="Security schemes used in the API")
    links: dict[str, Schema] = Field(default_factory=dict, description="Links used in the API")
    callbacks: dict[str, Schema] = Field(default_factory=dict, description="Callbacks used in the API")
    pathItems: dict[str, Schema] = Field(default_factory=dict, description="Path items used in the API")


class OpenAPI(BaseModel):
    """
    Represents an OpenAPI specification.
    """
    openapi: str = Field(..., description="The OpenAPI version")
    info: Info = Field(..., description="Information about the API")
    jsonSchemaDialect: str = Field(default="https://json-schema.org/draft/2020-12/schema", description="The JSON Schema dialect used")
    servers: list[Server] = Field(default_factory=list, description="List of servers where the API is hosted")
    paths: dict = Field(..., description="Available paths and operations for the API")
    webhooks: dict = Field(default_factory=dict, description="Webhooks available for the API")
    components: dict = Field(..., description="Components of the API, such as schemas and responses")
    security: list = Field(default_factory=list, description="Security schemes applied globally to the API")
    tags: list = Field(default_factory=list, description="Tags used by the API for categorization")
    externalDocs: dict = Field(default_factory=dict, description="External documentation for the API")

